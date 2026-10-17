"""Execute pinned official routing methods with tiny synthetic tensors, offline.

No claim about actual vocals: these regressions check routing and sampler knobs.
The audited source fixtures are byte-identical to bootstrap.lock.json.
"""
import ast
from dataclasses import dataclass, field, asdict
import hashlib
import inspect
import json
from pathlib import Path
import re
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from musicstudio.vocal_conditioning import (
    CONTROLLED_LYRICS, CONTROLLED_PROMPT, VocalConditioningMixin,
    make_generation_params, validate_vocal_request,
)

ROOT = Path(__file__).resolve().parents[1]


def request():
    return dict(memory_profile="phased_v1", use_lm=False, duration=30, seed=40601,
                vocal_test=True, lyrics=CONTROLLED_LYRICS, prompt=CONTROLLED_PROMPT)


class Tensor:
    """Minimal list-backed stand-in, not a replacement for native CUDA tests."""
    dtype = "torch.bfloat16"
    device = "cuda:0"

    def __init__(self, data):
        self.data = data

    @property
    def shape(self):
        result, value = [], self.data
        while isinstance(value, list):
            result.append(len(value))
            value = value[0] if value else None
        return tuple(result)

    def __getitem__(self, key):
        return Tensor(self.data[key])

    def __len__(self):
        return len(self.data)

    def bool(self):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.data

    def item(self):
        return self.data

    def flat(self):
        def flatten(data):
            if isinstance(data, list):
                for value in data:
                    yield from flatten(value)
            else:
                yield data
        return list(flatten(self.data))

    def all(self):
        return Tensor(all(self.flat()))

    def any(self):
        return Tensor(any(self.flat()))


class Tokenizer:
    pad_token_id = 0

    def __call__(self, text, truncation=False, max_length=None, return_tensors=None, **kwargs):
        ids = [int(hashlib.sha256(t.encode()).hexdigest()[:6], 16) for t in re.findall(r"\w+|[^\w\s]", text)]
        if truncation:
            ids = ids[:max_length]
        if return_tensors:
            return types.SimpleNamespace(input_ids=Tensor([ids]), attention_mask=Tensor([[True] * len(ids)]))
        return {"input_ids": ids}


def official_namespace():
    namespace = dict(dataclass=dataclass, field=field, asdict=asdict,
                     logger=Mock(), inspect=inspect, DIRECT_CONDITIONING_TASKS=frozenset())
    constants = ast.parse((ROOT / 'fuentes/vocal_constants.py').read_text())
    exec(compile(constants, '<pinned constants>', 'exec'), namespace)
    # Compile actual upstream classes with postponed annotations and no imports.
    # Exercise full class methods, not a manually reimplemented routing function.
    for filename, names in (
        ('vocal_prompt_utils.py', {'PromptMixin'}),
        ('vocal_conditioning_text.py', {'ConditioningTextMixin'}),
        ('vocal_service_generate_execute.py', {'ServiceGenerateExecuteMixin'}),
        ('inference.py', {'GenerationParams', 'GenerationConfig', 'GenerationResult', 'generate_music'}),
    ):
        tree = ast.parse((ROOT / 'fuentes' / filename).read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
        for node in nodes:
            if isinstance(node, ast.FunctionDef):
                node.decorator_list = []
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + nodes, type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), filename, 'exec'), namespace)
    return namespace


OFFICIAL = official_namespace()


class Base(OFFICIAL['PromptMixin'], OFFICIAL['ConditioningTextMixin'], OFFICIAL['ServiceGenerateExecuteMixin']):
    def _parse_metas(self, metas):
        return metas

    def _pad_sequences(self, seqs, length, value):
        return Tensor([seq.data + [value] * (length - len(seq)) for seq in seqs])

    def infer_lyric_embeddings(self, ids):
        return Tensor([[[float(i), 1.0] for i in ids.data[0]]])


class Host(VocalConditioningMixin, Base):
    def __init__(self):
        self.vocal_request = request()
        self.vocal_report = {}
        self.text_tokenizer = Tokenizer()
        self.silence_latent = object()
        self.model = None


def prepare(host, lyrics=None, language='es', metas=None):
    return host._prepare_text_conditioning_inputs(
        1, ['Fill the audio semantic mask based on the given conditions:'],
        [CONTROLLED_PROMPT], [CONTROLLED_LYRICS if lyrics is None else lyrics],
        [{} if metas is None else metas], [language], 1.0)


def sampler_kwargs(embedding, mask):
    payload = dict(lyric_hidden_states=embedding, lyric_attention_mask=mask,
                   is_covers=Tensor([False]), precomputed_lm_hints_25Hz=None)
    for name in ('text_hidden_states', 'text_attention_mask', 'refer_audio_acoustic_hidden_states_packed',
                 'refer_audio_order_mask', 'src_latents', 'chunk_mask',
                 'non_cover_text_hidden_states', 'non_cover_text_attention_masks'):
        payload[name] = None
    return dict(payload=payload, seed_param=40601, infer_steps=8, guidance_scale=1.0,
                audio_cover_strength=1.0, cover_noise_strength=0.0, infer_method='ode', use_adg=False,
                cfg_interval_start=0.0, cfg_interval_end=1.0, shift=3.0, timesteps=None)


class VocalTests(unittest.TestCase):
    def test_audit_fixtures_match_official_source_lock(self):
        lock = json.loads((ROOT / 'instalacion/bootstrap.lock.json').read_text())['source_files']
        for suffix in ('prompt_utils', 'conditioning_text', 'service_generate_execute'):
            source = (ROOT / f'fuentes/vocal_{suffix}.py').read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), lock[f'acestep/core/generation/handler/{suffix}.py'])
        self.assertEqual(hashlib.sha256((ROOT / 'fuentes/vocal_constants.py').read_bytes()).hexdigest(), lock['acestep/constants.py'])
        self.assertEqual(hashlib.sha256((ROOT / 'fuentes/inference.py').read_bytes()).hexdigest(), lock['acestep/inference.py'])

    def test_nonempty_singable_text_and_no_instrumental_or_internal_tokens(self):
        validate_vocal_request(request())
        for lyrics in ('', '[Verse]\n[Chorus]', '[Instrumental]\nhola', 'hola<|endoftext|>', '# Languages\nes\n# Lyric\nhola'):
            with self.subTest(lyrics=lyrics), self.assertRaises(ValueError):
                validate_vocal_request(dict(request(), lyrics=lyrics))

    def test_fixed_test_cannot_become_lm_or_batch_or_other_profile(self):
        for change in ({'use_lm': True}, {'duration': 45}, {'seed': 1}, {'memory_profile': 'streaming_aqt_v1'}, {'prompt': 'piano'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_vocal_request(dict(request(), **change))

    def test_official_api_really_passes_lyrics_without_lm(self):
        params = make_generation_params(OFFICIAL['GenerationParams'], request(), phased=True)
        self.assertEqual((params.shift, params.guidance_scale, params.instrumental, params.thinking), (3.0, 1.0, False, False))
        captured = {}
        # Full pinned generate_music runs until handler returns an intentional
        # failure, after capturing its actual argument values; no audio/model IO.
        def generate_music(captions=None, lyrics=None, vocal_language=None, audio_code_string=None,
                           task_type=None, shift=None, inference_steps=None, **kwargs):
            captured.update(locals())
            return {'success': False, 'error': 'synthetic stop before weights'}
        handler = types.SimpleNamespace(prepare_seeds=lambda *a: ([40601], ''), generate_music=generate_music)
        config = OFFICIAL['GenerationConfig'](batch_size=1, seeds=[40601], use_random_seed=False)
        result = OFFICIAL['generate_music'](handler, None, params, config)
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'synthetic stop before weights')
        self.assertEqual(captured['lyrics'], CONTROLLED_LYRICS)
        self.assertEqual(captured['captions'], CONTROLLED_PROMPT)
        self.assertEqual((captured['vocal_language'], captured['audio_code_string'], captured['task_type'], captured['shift']), ('es', '', 'text2music', 3.0))

    def test_previous_profile_keeps_its_sampling_schedule(self):
        params = make_generation_params(OFFICIAL['GenerationParams'], request(), phased=False)
        self.assertEqual(params.shift, 1.0)

    def test_official_tokens_embeddings_and_sampler_preserve_complete_lyrics(self):
        host = Host()
        result = prepare(host)
        fake_torch = types.SimpleNamespace(isfinite=lambda tensor: Tensor([True] * len(tensor.flat())))
        with patch.dict('sys.modules', torch=fake_torch):
            embedding = host.infer_lyric_embeddings(result[3])
        with tempfile.TemporaryDirectory() as directory:
            host.vocal_report_path = Path(directory) / 'vocal.json'
            output = host._build_service_generate_kwargs(**sampler_kwargs(embedding, result[4]))
            self.assertIs(output['lyric_hidden_states'], embedding)
            report = json.loads(host.vocal_report_path.read_text())
        self.assertTrue(report['sampler_boundary_checked'])
        self.assertEqual(report['language'], 'es')
        self.assertFalse(report['truncated'])
        self.assertEqual(report['vocal_presence'], 'PENDIENTE_DE_ESCUCHA')
        # All stored values must be serializable; no retained module/tensor.
        json.dumps(host.vocal_report)
        self.assertIsInstance(host._vocal_embedding_id, int)

    def test_lost_lyrics_or_metadata_language_override_stops_before_encoding(self):
        for kwargs in ({'lyrics': '[Instrumental]'}, {'language': 'en'}, {'metas': {'language': 'en'}}, {'metas': {'caption': 'piano solo'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                prepare(Host(), **kwargs)

    def test_overlong_lyrics_are_rejected_instead_of_truncated(self):
        host = Host()
        host.vocal_request.update(vocal_test=False, lyrics='cantar ' * 2050)
        with self.assertRaisesRegex(RuntimeError, '2048'):
            prepare(host, lyrics=host.vocal_request['lyrics'])

    def test_overlong_formatted_caption_is_rejected(self):
        host = Host()
        # Metadata consumes the caption budget too, even if caption alone fits.
        with self.assertRaisesRegex(RuntimeError, 'Caption truncado'):
            prepare(host, metas={'other': 'tempo ' * 280})

    def test_wrong_tokens_empty_or_nonfinite_embeddings_are_rejected(self):
        host = Host()
        result = prepare(host)
        with self.assertRaisesRegex(RuntimeError, 'perdio'):
            host.infer_lyric_embeddings(Tensor([[1]]))
        for output, finite in ((Tensor([[[0, 0]] * len(host._vocal_tokens)]), True),
                               (Tensor([[[1, 1]] * len(host._vocal_tokens)]), False)):
            fake = types.SimpleNamespace(isfinite=lambda tensor: Tensor(finite))
            with patch.dict('sys.modules', torch=fake), patch.object(Base, 'infer_lyric_embeddings', return_value=output):
                with self.assertRaisesRegex(RuntimeError, 'Embeddings'):
                    host.infer_lyric_embeddings(result[3])

    def test_sampler_rejects_lost_embedding_mask_cover_lm_and_wrong_schedule(self):
        host = Host()
        tokens = prepare(host)
        embedding = Tensor([[[1, 1]] * len(host._vocal_tokens)])
        host._vocal_embedding_id = id(embedding)
        for field, value in (('lyric_hidden_states', Tensor([])), ('lyric_attention_mask', Tensor([[False]])),
                             ('is_covers', Tensor([True])), ('precomputed_lm_hints_25Hz', object())):
            args = sampler_kwargs(embedding, tokens[4])
            args['payload'][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                host._build_service_generate_kwargs(**args)
        args = sampler_kwargs(embedding, tokens[4]); args['shift'] = 1.0
        with self.assertRaisesRegex(RuntimeError, 'Muestreo'):
            host._build_service_generate_kwargs(**args)


if __name__ == '__main__':
    unittest.main()
