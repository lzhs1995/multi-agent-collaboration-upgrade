"""Authenticate native Claude registration against the live kernel lifetime."""
import time


def matches_process(reg, process, *, observed_at):
    if (not isinstance(reg, dict) or type(reg.get("pid")) is not int
            or reg["pid"] != process["pid"] or reg.get("kind") != "interactive"):
        return False
    if "procStart" in reg:
        births = {time.strftime('%a %b %e %H:%M:%S %Y', clock(process["birth"][0]))
                  for clock in (time.gmtime, time.localtime)}
        return isinstance(reg["procStart"], str) and reg["procStart"] in births
    # 2.1.170 peerProtocol=1 uses JS Date.now() at registration. Accept only
    # timestamps inside this process's lifetime, at millisecond precision.
    # A stale PID registration predating the current kernel birth is refused.
    started = reg.get("startedAt")
    birth_ms = process["birth"][0] * 1000 + process["birth"][1] // 1000
    return (type(reg.get("peerProtocol")) is int and reg["peerProtocol"] == 1
            and reg.get("entrypoint") == "cli" and type(started) is int
            and birth_ms <= started <= int(observed_at * 1000))
