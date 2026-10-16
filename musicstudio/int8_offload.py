"""Validate INT8 with the pinned ACE-Step transfer helper, not raw Module.to.

PyTorch 2.10 + TorchAO 0.16 AQT v1 can dispatch
aten._has_compatible_shallow_copy_type from Module._apply in inference_mode.
The pinned ACE-Step helper catches that error and transfers tensor data.
"""
from __future__ import annotations

import importlib.util

from .common import verify_source


def make_offload_host():
    """Load the actual, hash-verified mixins without importing the entire pipeline."""
    source = verify_source()
    modules = []
    for name in ("init_service_memory_basic", "init_service_memory_transfer"):
        path = source / "acestep/core/generation/handler" / (name + ".py")
        spec = importlib.util.spec_from_file_location("musicstudio_pinned_" + name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"No se puede cargar el helper fijado: {name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    basic, transfer = modules

    class OffloadHost(transfer.InitServiceMemoryTransferMixin, basic.InitServiceMemoryBasicMixin):
        def _synchronize(self):
            import torch
            if torch.cuda.is_available():
                torch.cuda.synchronize(0)

    return OffloadHost()


def assert_int8_device(model, device):
    """Check both wrapper and AQT storage; upstream may log incomplete transfers."""
    import torch
    from torchao.dtypes.affine_quantized_tensor import AffineQuantizedTensor
    target = torch.device(device)
    if target.type == "cuda" and target.index is None:
        target = torch.device("cuda", torch.cuda.current_device())
    for name, tensor in list(model.named_parameters()) + list(model.named_buffers()):
        if tensor.device != target:
            raise RuntimeError(f"Offload incompleto: {name} en {tensor.device}, se esperaba {target}.")
    count = 0
    for name, module in model.named_modules():
        if not isinstance(module, torch.nn.Linear):
            continue
        weight = module.weight
        if not isinstance(weight, AffineQuantizedTensor):
            raise RuntimeError(f"Se perdio el tensor cuantizado en {name}.")
        impl = weight.tensor_impl
        if impl.int_data.dtype != torch.int8:
            raise RuntimeError(f"Se perdio el almacenamiento INT8 en {name}.")
        # AQT version 1 with PlainLayout, the configuration selected by ACE-Step.
        for field in ("int_data", "scale", "zero_point"):
            tensor = getattr(impl, field)
            if tensor is not None and tensor.device != target:
                raise RuntimeError(f"Offload incompleto: {name}.{field} en {tensor.device}.")
        count += 1
    if count == 0:
        raise RuntimeError("La prueba no contiene ninguna capa INT8 efectiva.")
    return count


def move_checked(host, model, device, dtype=None):
    """The same transfer path used by ACE-Step's _load_model_context."""
    host._recursive_to_device(model, device, dtype)
    return assert_int8_device(model, device)


def exercise_int8_offload(device="cuda", cycles=2, transfer=None):
    """Tiny real forward + round trips inside inference_mode; no musical weights."""
    import torch
    from torchao.quantization import Int8WeightOnlyConfig, quantize_
    host = make_offload_host()
    transfer = transfer or move_checked
    model = torch.nn.Sequential(torch.nn.Linear(64, 64, bias=True)).eval().to(dtype=torch.bfloat16)
    model.register_buffer("offload_probe", torch.zeros(1))
    sample_cpu = torch.randn(4, 64, dtype=torch.bfloat16)
    with torch.no_grad():
        reference = model(sample_cpu).float()
    quantize_(model, Int8WeightOnlyConfig(version=1))
    assert_int8_device(model, "cpu")
    original_int8 = model[0].weight.tensor_impl.int_data.clone()
    original_scale = model[0].weight.tensor_impl.scale.clone()
    errors = []
    with torch.inference_mode():
        for _ in range(cycles):
            transfer(host, model, device, torch.bfloat16)
            assert_int8_device(model, device)
            output = model(sample_cpu.to(device)).float().cpu()
            if not torch.isfinite(output).all().item():
                raise RuntimeError("Forward INT8 no finito despues de la carga al dispositivo.")
            torch.testing.assert_close(output, reference, rtol=0.10, atol=0.05)
            errors.append(float((output - reference).abs().max().item()))
            transfer(host, model, "cpu")
            assert_int8_device(model, "cpu")
            if not torch.equal(model[0].weight.tensor_impl.int_data, original_int8):
                raise RuntimeError("Los bytes INT8 cambiaron durante el offload.")
            if not torch.equal(model[0].weight.tensor_impl.scale, original_scale):
                raise RuntimeError("Las escalas INT8 cambiaron durante el offload.")
            if not torch.isfinite(model(sample_cpu)).all().item():
                raise RuntimeError("Forward en CPU no finito despues del offload.")
    return dict(transfer_method="ACE-Step._recursive_to_device" if transfer is move_checked else transfer.__name__, inference_mode=True,
                quantization="Int8WeightOnlyConfig(version=1)", cycles=cycles,
                tested_device=str(device), effective_int8_layers=assert_int8_device(model, "cpu"),
                integer_storage_and_scales_preserved=True, max_absolute_errors=errors)
