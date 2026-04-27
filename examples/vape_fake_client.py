#!/usr/bin/env python3
"""Small VAPE protocol smoke client for Linux-side testing."""

from __future__ import annotations

import argparse
import asyncio
import wave

import aiohttp


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8765/vape")
    parser.add_argument("--wav", required=True, help="16-bit mono WAV file to stream")
    parser.add_argument("--frame-ms", type=int, default=20)
    args = parser.parse_args()

    with wave.open(args.wav, "rb") as wav_file:
        if wav_file.getsampwidth() != 2 or wav_file.getnchannels() != 1:
            raise SystemExit("WAV must be 16-bit mono PCM")
        sample_rate = wav_file.getframerate()
        frame_bytes = int(sample_rate * args.frame_ms / 1000) * 2

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(args.url) as websocket:
                await websocket.send_json(
                    {
                        "type": "hello",
                        "device_id": "fake-vape-client",
                        "formats": [{"codec": "pcm_s16le", "sample_rate": sample_rate, "channels": 1}],
                    }
                )
                print(await websocket.receive_json())

                await websocket.send_json({"type": "wake_detected", "wake_word": "fake_wake"})
                print(await websocket.receive_json())

                while True:
                    chunk = wav_file.readframes(frame_bytes // 2)
                    if not chunk:
                        break
                    await websocket.send_bytes(chunk)
                    await asyncio.sleep(args.frame_ms / 1000)

                await websocket.send_json({"type": "audio_stop"})
                async for message in websocket:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        print(message.data)
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        print(f"audio bytes: {len(message.data)}")


if __name__ == "__main__":
    asyncio.run(main())
