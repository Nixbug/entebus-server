from py_mini_racer import MiniRacer, py_mini_racer
from app.src.constants import JSX_TIMEOUT_MS, JSX_MAX_MEMORY_BYTES
from app.src import exceptions


# Load the JS function into memory
# This can also be used to check if the JS code is proper
class DynamicFare:
    def __init__(
        self, jsCode, timeOutLimit=JSX_TIMEOUT_MS, maxMemorySize=JSX_MAX_MEMORY_BYTES
    ):
        try:
            self.jsContext = MiniRacer()
            self.timeOutLimit = timeOutLimit
            self.maxMemorySize = maxMemorySize
            getFare = self.jsContext.eval(f"{jsCode}; typeof getFare === 'function';")
            if not getFare:
                raise exceptions.InvalidFareFunction()
        except Exception:
            raise exceptions.InvalidFareFunction()

    def evaluate(self, ticketType, totalDistance, extra):
        try:
            return self.jsContext.call(
                "getFare",
                ticketType,
                totalDistance,
                extra,
                timeout=self.timeOutLimit,
                max_memory=self.maxMemorySize,
            )
        except py_mini_racer.JSTimeoutException:
            raise exceptions.JSTimeLimitExceeded()
        except py_mini_racer.JSOOMException:
            raise exceptions.JSMemoryLimitExceeded()
