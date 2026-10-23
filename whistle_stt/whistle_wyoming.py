# Copyright (c) 2026 Arkadiusz Raj, hijax_pl
"""Wyoming STT server around Cactus Whistle (cactus-needle)."""
import argparse
import array
import asyncio
import logging
from importlib.metadata import version
from functools import partial

import needle
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler, AsyncServer

LANGUAGES = ["en", "de", "fr", "es", "it", "nl", "pl"]
RATE = 16000
_LOG = logging.getLogger("whistle")


class Handler(AsyncEventHandler):
    def __init__(self, info, model, lock, args, *a, **kw):
        super().__init__(*a, **kw)
        self.info, self.model, self.lock, self.args = info, model, lock, args
        self.converter = AudioChunkConverter(rate=RATE, width=2, channels=1)
        self.language = args.language
        self.audio = bytearray()

    async def handle_event(self, event):
        if Describe.is_type(event.type):
            await self.write_event(self.info.event())
        elif Transcribe.is_type(event.type):
            lang = (Transcribe.from_event(event).language or "").split("-")[0].lower()
            self.language = lang if lang in LANGUAGES else self.args.language
        elif AudioStart.is_type(event.type):
            self.audio.clear()
        elif AudioChunk.is_type(event.type):
            self.audio += self.converter.convert(AudioChunk.from_event(event)).audio
        elif AudioStop.is_type(event.type):
            samples = array.array("f", (s / 32768 for s in array.array("h", bytes(self.audio))))
            # Model nie jest thread-safe - jedno rozpoznawanie naraz
            async with self.lock:
                r = await asyncio.get_running_loop().run_in_executor(
                    None, partial(self.model.transcribe, samples, language=self.language,
                                  keywords=self.args.keyword or None))
            text = r["text"].strip()
            _LOG.info("%.1fs audio -> %r", len(samples) / RATE, text)
            await self.write_event(Transcript(text=text, language=self.language).event())
            return False
        return True


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--uri", default="tcp://0.0.0.0:10300")
    p.add_argument("--language", default="pl", choices=LANGUAGES)
    p.add_argument("--keyword", action="append", default=[], help="keyword biasing, repeatable")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    model = needle.Whistle()
    _LOG.info("model %s, keywords %s", model.weights, args.keyword)
    who = Attribution(name="Cactus Compute", url="https://huggingface.co/Cactus-Compute/whistle")
    info = Info(asr=[AsrProgram(
        name="whistle", description="Cactus Whistle speech-to-text", attribution=who, installed=True,
        version=version("cactus-needle"),
        models=[AsrModel(name="whistle", description="Whistle 2.0", attribution=who, installed=True,
                         languages=LANGUAGES, version="2.0.0")])])
    lock = asyncio.Lock()
    await AsyncServer.from_uri(args.uri).run(partial(Handler, info, model, lock, args))


if __name__ == "__main__":
    asyncio.run(main())
