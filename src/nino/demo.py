from __future__ import annotations
from .runtime import InMemoryStateStore, NinoRuntime

if __name__ == "__main__":
    runtime = NinoRuntime(InMemoryStateStore())
    print(runtime.tick("agent-demo", {"intent": "greeting", "text": "hola"}))
    print(runtime.tick("agent-demo", {"intent": "follow_up", "text": "te acuerdas de mí?"}))
