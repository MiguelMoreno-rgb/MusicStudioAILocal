"""Conservative inference-only transfer for pinned AQT v1 / PlainLayout.

No edits to vendor sources. Direct streaming transfer avoids Module.to's
known failing speculative copy; dense transfers combine device/dtype once.
No dequantization, CPU model clone, state_dict, pinned CPU cache, or async DMA.
"""
from contextlib import contextmanager
import gc


def storage_bytes(tensor):
    """Internal tensor payload bytes, not RSS or unique resident storage.

    Shared views can overlap; this is transfer-size metadata, not RAM usage.
    """
    if hasattr(tensor, 'tensor_impl'):
        impl = tensor.tensor_impl
        return sum(t.numel()*t.element_size() for t in (impl.int_data, impl.scale, impl.zero_point) if t is not None)
    return tensor.numel()*tensor.element_size()


def move_streaming(host, model, device, dtype=None, trace=None):
    import torch
    from torchao.dtypes.affine_quantized_tensor import AffineQuantizedTensor
    from torchao.dtypes.uintx.plain_layout import PlainAQTTensorImpl
    target = torch.device(device)
    if target.type == 'cuda' and target.index is None:
        target = torch.device('cuda', torch.cuda.current_device())
    moved_parameters, moved_buffers, visited = {}, {}, set()
    stats = dict(parameters=0, quantized_parameters=0, shared_parameter_reuses=0,
                 source_payload_bytes=0, largest_parameter_payload_bytes=0)

    def emit(event, **details):
        if trace is not None:
            trace.emit('transfer.tensor', event, target=str(target), **details)

    def parameter(param, name):
        identity = id(param)
        if identity in moved_parameters:
            stats['shared_parameter_reuses'] += 1
            return moved_parameters[identity]
        if param.grad is not None:
            raise RuntimeError('La transferencia optimizada es solo para inferencia sin gradientes.')
        quantized = isinstance(param, AffineQuantizedTensor)
        size = storage_bytes(param)
        stats['parameters'] += 1
        stats['source_payload_bytes'] += size
        stats['largest_parameter_payload_bytes'] = max(stats['largest_parameter_payload_bytes'], size)
        emit('begin', name=name, source=str(param.device), payload_bytes=size, quantized=quantized)
        if quantized:
            if not isinstance(param.tensor_impl, PlainAQTTensorImpl) or param.tensor_impl.int_data.dtype != torch.int8:
                raise RuntimeError('Solo se admite INT8 AQT v1 PlainLayout en esta optimizacion.')
            if dtype is not None and dtype != param.dtype:
                raise RuntimeError('No se cambia el dtype logico de los pesos INT8.')
            # Same supported torchao primitive as pinned ACE-Step's manual helper.
            # Existing target tensors are reused; no speculative model.to first.
            fields = (param.tensor_impl.int_data, param.tensor_impl.scale, param.tensor_impl.zero_point)
            if param.device == target and all(t is None or t.device == target for t in fields):
                result = param
            else:
                result = torch.nn.Parameter(param._apply_fn_to_data(lambda t: t.to(target)), requires_grad=param.requires_grad)
            stats['quantized_parameters'] += 1
            impl = result.tensor_impl
            if result.device != target or any(t is not None and t.device != target for t in (impl.int_data, impl.scale, impl.zero_point)):
                raise RuntimeError('Transferencia INT8 incompleta: ' + name)
        else:
            desired = dtype if dtype is not None and param.is_floating_point() else param.dtype
            # A single allocation, unlike .to(device).to(dtype).
            param.data = param.data.to(device=target, dtype=desired)
            result = param
        moved_parameters[identity] = result  # No reference to the OLD CPU AQT.
        emit('end', name=name, payload_bytes=size, quantized=quantized)
        return result

    def walk(module, prefix=''):
        if id(module) in visited:
            return
        visited.add(id(module))
        for name in module._parameters:
            old = module._parameters[name]
            if old is not None:
                module._parameters[name] = parameter(old, prefix+name)
                del old  # Do not retain a moved CPU parameter across iterations.
        for name in module._buffers:
            buf = module._buffers[name]
            if buf is not None:
                identity = id(buf)
                if identity not in moved_buffers:
                    emit('begin', name=prefix+name, buffer=True, source=str(buf.device), payload_bytes=storage_bytes(buf))
                    moved_buffers[identity] = buf.to(target)
                    emit('end', name=prefix+name, buffer=True)
                module._buffers[name] = moved_buffers[identity]
            del buf
        for name, child in module._modules.items():
            if child is not None:
                walk(child, prefix+name+'.')
        # Preserve upstream coverage of nonregistered adapters; don't remove it.
        for name, child in vars(module).items():
            if not name.startswith('_') and isinstance(child, torch.nn.Module):
                walk(child, prefix+name+'.')
    with torch.no_grad():
        walk(model)
    if target.type == 'cuda':
        host._synchronize()  # Blocking transfers; don't free source before completion.
    # Validate buffers too; never swallow incomplete transfers.
    for name, tensor in list(model.named_parameters()) + list(model.named_buffers()):
        if tensor.device != target:
            raise RuntimeError('Transferencia incompleta: ' + name)
    return stats


class StreamingOffloadMixin:
    """Opt-in at our handler subclass only, leaving ACE sources intact."""
    memory_trace = None

    def _recursive_to_device(self, model, device, dtype=None):
        trace = self.memory_trace
        if trace is None:
            raise RuntimeError('Falta telemetria de transferencia.')
        with trace.span('transfer.model', target=str(device)):
            trace.emit('transfer.gc', 'begin')
            collected = gc.collect()  # Collect before the peak as well as upstream's after.
            trace.emit('transfer.gc', 'end', collected=collected)
            if self._has_quantized_params(model):
                stats = move_streaming(self, model, device, dtype, trace)
                trace.emit('transfer.model', 'summary', strategy='streaming_aqt_v1', **stats)
            else:
                # Dense VAE/text encoder keep their upstream semantics.
                super()._recursive_to_device(model, device, dtype)

    @contextmanager
    def _load_model_context(self, model_name):
        with self.memory_trace.span('component.'+model_name):
            with super()._load_model_context(model_name):
                with self.memory_trace.span('compute.'+model_name):
                    yield
