"""Fail-closed lyric routing checks for the one-shot phased Turbo profile.

Only small token lists/scalars survive a stage. No hooks, module references,
weight copies, source edits, downloads or changes to memory telemetry.
"""
import hashlib
import re

CONTROLLED_PROMPT = (
    "Male solo vocal singing clearly in Mexican Spanish from the opening, "
    "romantic corrido tumbado song, intimate expressive baritone, "
    "requinto and acoustic twelve-string guitar accompaniment, warm bass, "
    "90 BPM, bittersweet love song, lead vocal prominent throughout."
)
CONTROLLED_LYRICS = """[Verse]
Te llevo cerca de mi guitarra,
tu nombre vuelve con mi cantar.

[Chorus]
Si tú me escuchas, vuelve esta noche,
que entre mis brazos tienes hogar."""


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_vocal_request(request):
    lyrics = request.get("lyrics")
    if not isinstance(lyrics, str) or not re.sub(r"\[[^\]]*\]", "", lyrics).strip():
        raise ValueError("La prueba vocal exige letra cantable, no solo etiquetas.")
    if re.search(r"\[\s*instrumental\s*\]|<\|[^>]*\|>|#\s*(Languages|Lyric)\b", lyrics, re.I):
        raise ValueError("Letra incompatible: no usar [Instrumental], tokens especiales ni cabeceras internas.")
    if request.get("vocal_test", False):
        if (request.get("memory_profile") != "phased_v1" or request.get("use_lm") is not False
                or request.get("duration") != 30 or request.get("seed") != 40601
                or request.get("prompt") != CONTROLLED_PROMPT or lyrics != CONTROLLED_LYRICS):
            raise ValueError("La prueba vocal controlada exige su configuracion fija y una sola toma.")


def make_generation_params(factory, request, *, phased):
    """Keep the prior memory profile's schedule; use the documented Turbo shift in phases."""
    if phased:
        validate_vocal_request(request)
    return factory(
        task_type="text2music", caption=request["prompt"], lyrics=request["lyrics"],
        instrumental=False, vocal_language="es", duration=30, bpm=90, timesignature="4",
        inference_steps=8, seed=request["seed"], thinking=request["use_lm"],
        shift=3.0 if phased else 1.0, guidance_scale=1.0,
        use_cot_metas=False, use_cot_caption=False, use_cot_lyrics=False, use_cot_language=False,
    )


def token_rows(tensor):
    # At most 2048 token integers, never weights/embedding arrays.
    return tensor.detach().cpu().tolist()


class VocalConditioningMixin:
    """Observe the pinned handler's tokenization, embedding and sampler boundary."""

    def _prepare_text_conditioning_inputs(self, batch_size, instructions, captions,
                                          lyrics, parsed_metas, vocal_languages,
                                          audio_cover_strength, **kwargs):
        request = self.vocal_request
        validate_vocal_request(request)
        actual_captions, languages = self._extract_caption_and_language(parsed_metas, captions, vocal_languages)
        if batch_size != 1 or lyrics != [request["lyrics"]] or languages != ["es"] or actual_captions != [request["prompt"]]:
            raise RuntimeError("El condicionamiento vocal cambio entre la solicitud y el tokenizer.")
        formatted = self._format_lyrics(lyrics[0], "es")
        expected = self.text_tokenizer(formatted, truncation=False)["input_ids"]
        if not expected or len(expected) > 2048:
            raise RuntimeError("La letra excede el limite real de 2048 tokens; no se truncara silenciosamente.")
        result = super()._prepare_text_conditioning_inputs(
            batch_size, instructions, captions, lyrics, parsed_metas, vocal_languages,
            audio_cover_strength, **kwargs)
        if token_rows(result[3]) != [expected] or not result[4].all().item():
            raise RuntimeError("Los tokens o la mascara de letra no coinciden con la solicitud completa.")
        # The official handler returns the formatted caption and lyrics together.
        text_prompt, separator, tail = result[0][0].rpartition("\n\n" + formatted)
        if not separator or tail:
            raise RuntimeError("Formato de condicionamiento oficial inesperado.")
        caption_tokens = self.text_tokenizer(text_prompt, truncation=False)["input_ids"]
        if len(caption_tokens) > 256 or token_rows(result[1]) != [caption_tokens]:
            raise RuntimeError("Caption truncado o alterado antes del encoder; acortar el prompt.")
        self._vocal_tokens = expected
        self.vocal_report.update(
            lyrics_sha256=digest(lyrics[0]), caption_sha256=digest(actual_captions[0]),
            formatted_lyrics_sha256=digest(formatted), language="es",
            lyric_tokens=len(expected), caption_tokens=len(caption_tokens),
            tokenization_matches_request=True, truncated=False,
        )
        return result

    def infer_lyric_embeddings(self, lyric_token_ids):
        if token_rows(lyric_token_ids) != [self._vocal_tokens]:
            raise RuntimeError("La entrada de embed_tokens perdio la letra.")
        output = super().infer_lyric_embeddings(lyric_token_ids)
        import torch
        if (len(output.shape) != 3 or tuple(output.shape[:2]) != (1, len(self._vocal_tokens))
                or not torch.isfinite(output).all().item() or not output.any().item()):
            raise RuntimeError("Embeddings de letra vacios, no finitos o con dimensiones incompatibles.")
        self._vocal_embedding_id = id(output)  # Never retain a tensor/module reference.
        self.vocal_report["embedding"] = dict(shape=list(output.shape), dtype=str(output.dtype),
                                               device=str(output.device), finite=True, nonzero=True)
        return output

    def _build_service_generate_kwargs(self, **kwargs):
        result = super()._build_service_generate_kwargs(**kwargs)
        if (id(result["lyric_hidden_states"]) != self._vocal_embedding_id
                or token_rows(result["lyric_attention_mask"]) != [[True] * len(self._vocal_tokens)]
                or result["is_covers"].any().item()
                or result["precomputed_lm_hints_25Hz"] is not None):
            raise RuntimeError("El DiT no recibira el condicionamiento vocal completo de text2music sin LM.")
        if result["shift"] != 3.0 or result["infer_steps"] != 8 or result.get("timesteps") is not None:
            raise RuntimeError("Muestreo Turbo por fases distinto del perfil vocal auditado.")
        self.vocal_report.update(status="routing_checked", sampler_boundary_checked=True, shift=result["shift"],
                                 inference_steps=result["infer_steps"], lm_hints=False,
                                 is_cover=False, vocal_presence="PENDIENTE_DE_ESCUCHA")
        # Write once at this boundary. No reader/marker protocol or telemetry changes.
        from .common import write_json
        write_json(self.vocal_report_path, self.vocal_report)
        return result
