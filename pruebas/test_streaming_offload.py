"""Real AQT v1 regression with synthetic weights only, never musical models."""
import importlib.util
import os
from pathlib import Path
from contextlib import contextmanager
import unittest
from unittest.mock import patch
from musicstudio import common
from musicstudio.streaming_offload import move_streaming, storage_bytes

HAS_TORCH=importlib.util.find_spec('torch') is not None and importlib.util.find_spec('torchao') is not None

class Events:
    def __init__(self):self.events=[]
    def emit(self,operation,event,**details):self.events.append((operation,event,details))
    @contextmanager
    def span(self,operation,**details):
        self.emit(operation,'begin',**details)
        yield
        self.emit(operation,'end',**details)

@unittest.skipUnless(HAS_TORCH,'Requiere los paquetes existentes de Torch/TorchAO; no instala nada.')
class StreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch,torchao
        if torch.__version__.split('+')[0]!='2.10.0' or torchao.__version__!='0.16.0':raise RuntimeError('Versiones fuera del stack fijado')

    def model(self,bias=True):
        import torch
        from torchao.quantization import Int8WeightOnlyConfig,quantize_
        torch.manual_seed(4060)
        m=torch.nn.Sequential(torch.nn.Linear(64,64,bias=bias)).eval().to(dtype=torch.bfloat16)
        quantize_(m,Int8WeightOnlyConfig(version=1))
        m.register_buffer('probe',torch.zeros(2))
        return m

    def host(self):
        from musicstudio.int8_offload import make_offload_host
        source=Path(os.environ.get('MUSICSTUDIO_TEST_SOURCE',common.source_root()))
        if not source.is_dir():self.skipTest('Falta fuente ACE-Step fijada; no se descarga.')
        with patch.object(common,'source_root',return_value=source):return make_offload_host()

    def test_direct_path_never_attempts_failing_module_to_and_keeps_forward(self):
        import torch
        from musicstudio.int8_offload import assert_int8_device
        model=self.model();host=self.host();trace=Events();x=torch.randn(4,64,dtype=torch.bfloat16)
        integer=model[0].weight.tensor_impl.int_data.clone();scale=model[0].weight.tensor_impl.scale.clone()
        with torch.inference_mode():
            before=model(x).clone()
            with patch.object(model,'to',side_effect=AssertionError('speculative model.to forbidden')):
                stats=move_streaming(host,model,'cpu',torch.bfloat16,trace)
            torch.testing.assert_close(model(x),before,rtol=0,atol=0)
        self.assertEqual(stats['quantized_parameters'],1);assert_int8_device(model,'cpu')
        self.assertTrue(torch.equal(integer,model[0].weight.tensor_impl.int_data))
        self.assertTrue(torch.equal(scale,model[0].weight.tensor_impl.scale))
        self.assertEqual(trace.events[0][1],'begin');self.assertEqual(trace.events[-1][1],'end')

    def test_meta_copy_requests_drop_from_two_to_one(self):
        import torch
        from torchao.dtypes.uintx.plain_layout import PlainAQTTensorImpl
        original=PlainAQTTensorImpl.to
        counts=[]
        def spy(t,*a,**kw):counts.append(1);return original(t,*a,**kw)
        host=self.host()
        with torch.inference_mode(),patch.object(PlainAQTTensorImpl,'to',spy):
            old=self.model(False)
            counts.clear();host._recursive_to_device(old,'meta',torch.bfloat16);upstream=len(counts)
            new=self.model(False)
            counts.clear();move_streaming(host,new,'meta',torch.bfloat16);optimized=len(counts)
        self.assertEqual(upstream,2);self.assertEqual(optimized,1)
        # Meta proves copy request count, NOT actual CPU or CUDA RAM savings.

    def test_shared_quantized_parameter_stays_shared_after_transfer(self):
        import torch
        m=self.model(False);other=torch.nn.Linear(64,64,bias=False)
        other.weight=m[0].weight;m.add_module('shared',other)
        with torch.inference_mode():stats=move_streaming(self.host(),m,'meta',torch.bfloat16)
        self.assertIs(m[0].weight,m.shared.weight);self.assertEqual(stats['shared_parameter_reuses'],1)

    def test_noop_reuses_original_quantized_wrapper_and_measures_physical_int8(self):
        m=self.model();before=m[0].weight
        stats=move_streaming(self.host(),m,'cpu',None)
        self.assertIs(m[0].weight,before)
        self.assertLess(storage_bytes(before),before.numel()*before.element_size())
        self.assertEqual(stats['source_payload_bytes'],storage_bytes(before)+storage_bytes(m[0].bias))

    def test_failures_propagate_and_do_not_silently_dequantize(self):
        import torch
        from torchao.dtypes.affine_quantized_tensor import AffineQuantizedTensor
        m=self.model(False);before=m[0].weight
        with patch.object(AffineQuantizedTensor,'_apply_fn_to_data',side_effect=NotImplementedError('real transfer failure')):
            with self.assertRaisesRegex(NotImplementedError,'real transfer failure'):
                move_streaming(self.host(),m,'meta',torch.bfloat16)
        self.assertIs(m[0].weight,before)
        m=self.model(False)  # A failed transfer is not transactional; never retry it.
        with self.assertRaisesRegex(RuntimeError,'dtype logico'):
            move_streaming(self.host(),m,'cpu',torch.float32)

    def test_handler_uses_optimized_path_and_preserves_adapter_traversal(self):
        import torch
        from musicstudio.streaming_offload import StreamingOffloadMixin
        upstream=self.host()
        Host=type('IntegratedHandler',(StreamingOffloadMixin,type(upstream)),{})
        host=Host();host.memory_trace=Events();m=self.model()
        adapter=torch.nn.Linear(8,8).eval()
        object.__setattr__(m,'plain_adapter',adapter)
        with torch.inference_mode(),patch.object(m,'to',side_effect=AssertionError('no speculative copy')):
            host._recursive_to_device(m,'cpu',torch.bfloat16)
        self.assertEqual(adapter.weight.dtype,torch.bfloat16)
        summaries=[e for e in host.memory_trace.events if e[1]=='summary']
        self.assertEqual(summaries[0][2]['strategy'],'streaming_aqt_v1')
        self.assertEqual(summaries[0][2]['quantized_parameters'],1)

    def test_cuda_round_trip_and_forward_exact_with_same_quantized_weights(self):
        import torch
        if not torch.cuda.is_available():self.skipTest('Requiere CUDA real; CPU/meta no la acreditan.')
        m=self.model();host=self.host();x=torch.randn(4,64,dtype=torch.bfloat16)
        original=m[0].weight.tensor_impl.int_data.clone();scale=m[0].weight.tensor_impl.scale.clone()
        with torch.inference_mode():
            reference=m(x).clone()
            for _ in range(2):
                move_streaming(host,m,'cuda',torch.bfloat16)
                output=m(x.cuda()).cpu()
                torch.testing.assert_close(output,reference,rtol=.1,atol=.05)
                move_streaming(host,m,'cpu')
                self.assertTrue(torch.equal(original,m[0].weight.tensor_impl.int_data))
                self.assertTrue(torch.equal(scale,m[0].weight.tensor_impl.scale))
