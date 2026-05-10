"""Throwaway smoke test for qq_launcher. Safe to delete."""
import asyncio
from app.services.sync.qq_launcher import check_docker, _extract_token, status


async def main():
    d = await check_docker()
    print("check_docker ->", d)

    samples = [
        "[QCE] Access Token: abc123DEF_456-xyz987654321",
        "access_token: ThisIsA_Very-LongToken_1234567890",
        "QCE Token = qce-abcdefghij1234567890",
        "Listening on 40653",
    ]
    for line in samples:
        print(repr(line), "->", _extract_token(line))

    s = await status()
    print("status ->", s)


asyncio.run(main())
