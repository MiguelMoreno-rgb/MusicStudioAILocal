"""Tiny native tensor checks; no musical checkpoint, download or generation."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from musicstudio.vocal_conditioning import VocalConditioningMixin
from pruebas.test_vocal_conditioning import request


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requiere PyTorch existente; no se instala.')
class NativeVocalTests(unittest.TestCase):
    def test_real_bf16_lyric_embedding_and_sampler_payload(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest('Requiere CUDA real para acreditar BF16 y liberacion en GPU.')
        # Actual torch tensors and embedding operator, deliberately tiny/random.
        class Base:
            def infer_lyric_embeddings(self, ids):
                return self.table(ids)

            def _build_service_generate_kwargs(self, **kwargs):
                return kwargs['payload']

        class Host(VocalConditioningMixin, Base):
            pass

        host = Host()
        host.vocal_request = request()
        host.vocal_report = {}
        host._vocal_tokens = [1, 2, 3]
        host.table = torch.nn.Embedding(8, 16, device='cuda', dtype=torch.bfloat16).eval()
        ids = torch.tensor([[1, 2, 3]], device='cuda')
        with torch.inference_mode(), tempfile.TemporaryDirectory() as directory:
            host.vocal_report_path = Path(directory) / 'vocal.json'
            embeddings = host.infer_lyric_embeddings(ids)
            payload = dict(lyric_hidden_states=embeddings,
                           lyric_attention_mask=torch.ones(1, 3, device='cuda', dtype=torch.bool),
                           is_covers=torch.tensor([False], device='cuda'), precomputed_lm_hints_25Hz=None,
                           shift=3.0, infer_steps=8)
            actual = host._build_service_generate_kwargs(payload=payload)
            self.assertIs(actual['lyric_hidden_states'], embeddings)
            self.assertEqual(embeddings.dtype, torch.bfloat16)
            self.assertEqual(host.vocal_report['embedding']['shape'], [1, 3, 16])
            self.assertTrue(host.vocal_report_path.is_file())
            # Removing the module does not invalidate its embedding output.
            del host.table
            torch.cuda.synchronize()
            self.assertTrue(torch.isfinite(embeddings).all().item())


if __name__ == '__main__':
    unittest.main()
