from py_mini_racer import MiniRacer, py_mini_racer
from app.src.constants import JSX_TIMEOUT_MS, JSX_MAX_MEMORY_BYTES
from app.src import exceptions


# Load the JS function into a V8 context.
class DynamicFare:
    def __init__(
        self,
        js_code,
        time_out_limit=JSX_TIMEOUT_MS,
        max_memory_size=JSX_MAX_MEMORY_BYTES,
    ):
        self.js_context = MiniRacer()
        self.time_out_limit = time_out_limit
        self.max_memory_size = max_memory_size
        try:
            get_fare = self.js_context.eval(
                f"{js_code}; typeof getFare === 'function';",
                timeout=self.time_out_limit,
            )
            if not get_fare:
                raise exceptions.InvalidFareFunction()
        except py_mini_racer.JSTimeoutException:
            raise exceptions.JSTimeLimitExceeded()
        except py_mini_racer.JSOOMException:
            raise exceptions.JSMemoryLimitExceeded()
        except (
            py_mini_racer.JSParseException,
            py_mini_racer.JSEvalException,
            py_mini_racer.JSConversionException,
        ):
            raise exceptions.InvalidFareFunction()

    def evaluate(self, ticket_type, total_distance, extra):
        try:
            return self.js_context.call(
                "getFare",
                ticket_type,
                total_distance,
                extra,
                timeout=self.time_out_limit,
                max_memory=self.max_memory_size,
            )
        except py_mini_racer.JSTimeoutException:
            raise exceptions.JSTimeLimitExceeded()
        except py_mini_racer.JSOOMException:
            raise exceptions.JSMemoryLimitExceeded()
        except (
            py_mini_racer.JSParseException,
            py_mini_racer.JSEvalException,
            py_mini_racer.JSConversionException,
        ):
            raise exceptions.InvalidFareFunction()
