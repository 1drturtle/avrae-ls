import collections
import itertools
import re
import string
from typing import ClassVar, Iterator


class BadArgument(Exception):
    pass


class ExpectedClosingQuoteError(Exception):
    def __init__(self, quote):
        super().__init__(quote)
        self.quote = quote

    def __str__(self):
        return f"Expected closing quote {self.quote}"


class InvalidArgument(Exception):
    pass


class StringView:
    """Minimal stand-in for disnake.ext.commands.view.StringView used by argparser."""

    def __init__(self, buffer: str):
        self.buffer = buffer
        self.index = 0

    @property
    def current(self):
        if self.eof:
            return None
        return self.buffer[self.index]

    @property
    def eof(self) -> bool:
        return self.index >= len(self.buffer)

    def skip_ws(self):
        while not self.eof and self.buffer[self.index].isspace():
            self.index += 1

    def get(self):
        if self.eof:
            return None
        ch = self.buffer[self.index]
        self.index += 1
        return ch

    def undo(self):
        if self.index > 0:
            self.index -= 1


EPHEMERAL_ARG_RE = re.compile(r"(\S+)(\d+)")
SINGLE_ARG_RE = re.compile(r"([a-zA-Z]\S*(?<!\d))(\d+)?")  # g1: flag name g2: ephem?
FLAG_ARG_RE = re.compile(r"-+([a-zA-Z]\S*(?<!\d))(\d+)?")  # g1: flag name g2: ephem?
SINGLE_ARG_EXCEPTIONS = {"-i", "-h", "-v"}


def argsplit(args: str):
    view = CustomStringView(args.strip())
    args_out = []
    while not view.eof:
        view.skip_ws()
        word = view.get_quoted_word()
        if word is not None:
            args_out.append(word)
    return args_out


# ==== argparse ====
class Argument:
    def __init__(self, name: str, value, pos: int):
        self.name = name
        self.value = value
        self.pos = pos

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name!r} value={self.value!r} pos={self.pos}>"

    def __eq__(self, other):
        return self.name == other.name and self.value == other.value and self.pos == other.pos


class EphemeralArgument(Argument):
    def __init__(self, name: str, value, pos: int, uses: int):
        super().__init__(name, value, pos)
        self.uses = uses
        self.used = 0

    def has_remaining_uses(self):
        return self.used < self.uses

    def __repr__(self):
        return (
            f"<{type(self).__name__} name={self.name!r} value={self.value!r} pos={self.pos} uses={self.uses} "
            f"used={self.used}>"
        )

    def __eq__(self, other):
        return (
            self.name == other.name and self.value == other.value and self.pos == other.pos and self.uses == other.uses
        )


def _argparse_arg(name: str, ephem: str | None, value, idx: int, parse_ephem: bool) -> Argument:
    if ephem and parse_ephem:
        return EphemeralArgument(name=name, value=value, pos=idx, uses=int(ephem))
    elif ephem:
        return Argument(name=name + ephem, value=value, pos=idx)
    else:
        return Argument(name=name, value=value, pos=idx)


def _argparse_iterator(args: list[str], parse_ephem: bool) -> Iterator[Argument]:
    flag_arg_state = None  # state: name, ephem?
    idx = 0
    for idx, arg in enumerate(args):
        # prio: single arg exceptions, flag args, values, single args
        if arg in SINGLE_ARG_EXCEPTIONS:
            if flag_arg_state is not None:
                name, ephem = flag_arg_state
                yield _argparse_arg(name, ephem, True, idx - 1, parse_ephem)
                flag_arg_state = None
            yield Argument(name=arg.lstrip("-"), value=True, pos=idx)
        elif match := FLAG_ARG_RE.fullmatch(arg):
            if flag_arg_state is not None:
                name, ephem = flag_arg_state
                yield _argparse_arg(name, ephem, True, idx - 1, parse_ephem)
            flag_arg_state = match.group(1), match.group(2)
        elif flag_arg_state is not None:
            name, ephem = flag_arg_state
            yield _argparse_arg(name, ephem, arg, idx - 1, parse_ephem)
            flag_arg_state = None
        elif match := SINGLE_ARG_RE.fullmatch(arg):
            name = match.group(1)
            ephem = match.group(2)
            yield _argparse_arg(name, ephem, True, idx, parse_ephem)
        # else: the current element at the head is junk

    if flag_arg_state is not None:
        name, ephem = flag_arg_state
        yield _argparse_arg(name, ephem, True, idx, parse_ephem)


# --- main entrypoint ---
def argparse(args, character=None, splitter=argsplit, parse_ephem=True) -> "ParsedArguments":
    """
    Given an argument string, returns the parsed arguments using the argument nondeterministic finite automaton.
    If *character* is given, evaluates {}-style math inside the string before parsing.
    If the argument is a string, uses *splitter* to split the string into args.
    If *parse_ephem* is False, arguments like ``-d1`` are saved literally rather than as an ephemeral argument.
    """
    if isinstance(args, str):
        args = splitter(args)

    if character:
        from aliasing.evaluators import MathEvaluator

        evaluator = MathEvaluator.with_character(character)
        args = [evaluator.transformed_str(a) for a in args]

    parsed_args = list(_argparse_iterator(args, parse_ephem))
    return ParsedArguments(parsed_args)


class ParsedArguments:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = [
        "get",
        "last",
        "adv",
        "join",
        "ignore",
        "update",
        "update_nx",
        "set_context",
        "add_context",
    ]

    def __init__(self, args: list[Argument]):
        self._parsed = collections.defaultdict(lambda: [])
        for arg in args:
            self._parsed[arg.name].append(arg)
        self._current_context = None
        self._contexts = {}  # type: dict[..., ParsedArguments]

    @classmethod
    def from_dict(cls, d):
        inst = cls([])
        for key, value in d.items():
            inst[key] = value
        return inst

    @classmethod
    def empty_args(cls):
        return cls([])

    def get(self, arg, default=None, type_=str, ephem=False):
        if default is None:
            default = []
        parsed = list(self._get_values(arg, ephem=ephem))
        if not parsed:
            return default
        try:
            return [type_(v) for v in parsed]
        except (ValueError, TypeError):
            raise InvalidArgument(f"One or more arguments cannot be cast to {type_.__name__} (in `{arg}`)")

    def last(self, arg, default=None, type_=str, ephem=False):
        last_arg = self._get_last(arg, ephem=ephem)
        if last_arg is None:
            return default
        try:
            return type_(last_arg)
        except (ValueError, TypeError):
            raise InvalidArgument(f"{last_arg} cannot be cast to {type_.__name__} (in `{arg}`)")

    def adv(self, eadv=False, boolwise=False, ephem=False, custom: dict = None):
        adv_str, dis_str, ea_str = "adv", "dis", "eadv"
        if custom is not None:
            if "adv" in custom:
                adv_str = custom["adv"]
            if "dis" in custom:
                dis_str = custom["dis"]
            if "eadv" in custom:
                ea_str = custom["eadv"]

        adv_arg = self.last(adv_str, default=False, type_=bool, ephem=ephem)
        dis_arg = self.last(dis_str, default=False, type_=bool, ephem=ephem)
        ea_arg = eadv and self.last(ea_str, default=False, type_=bool, ephem=ephem)

        if ea_arg and not dis_arg:
            out = 2
        elif dis_arg and not (adv_arg or ea_arg):
            out = -1
        elif adv_arg and not dis_arg:
            out = 1
        else:
            out = 0

        if not boolwise:
            return out
        else:
            return {-1: False, 0: None, 1: True}.get(out)

    def join(self, arg, connector: str, default=None, ephem=False):
        return connector.join(self.get(arg, ephem=ephem)) or default

    def ignore(self, arg):
        del self[arg]
        for context in self._contexts.values():
            del context[arg]

    def update(self, new):
        for k, v in new.items():
            self[k] = v

    def update_nx(self, new):
        for k, v in new.items():
            if k not in self and v is not None:
                self[k] = v

    @staticmethod
    def _yield_from_iterable(iterable: Iterator[Argument], ephem: bool):
        for value in iterable:
            if not ephem and isinstance(value, EphemeralArgument):
                continue
            elif isinstance(value, EphemeralArgument):
                if not value.has_remaining_uses():
                    continue
                value.used += 1
            yield value.value

    def _get_values(self, arg, ephem=False):
        iterable = self._parsed[arg]
        if self._current_context in self._contexts:
            iterable = itertools.chain(self._parsed[arg], self._contexts[self._current_context]._parsed[arg])
        yield from self._yield_from_iterable(iterable, ephem)

    def _get_last(self, arg, ephem=False):
        iterable = reversed(self._parsed[arg])
        if self._current_context in self._contexts:
            iterable = itertools.chain(
                reversed(self._contexts[self._current_context]._parsed[arg]), reversed(self._parsed[arg])
            )
        return next((self._yield_from_iterable(iterable, ephem)), None)

    def set_context(self, context):
        self._current_context = context

    def add_context(self, context, args):
        if isinstance(args, dict):
            if all(
                isinstance(k, (collections.UserString, str))
                and isinstance(v, (collections.UserList, list))
                and all(isinstance(i, (collections.UserString, str)) for i in v)
                for k, v in args.items()
            ):
                args = ParsedArguments.from_dict(args)
            else:
                raise InvalidArgument(f"Argument is not in the format dict[str, list[str]] (in {args})")
        elif not isinstance(args, ParsedArguments):
            raise InvalidArgument(f"Argument is not a dict or ParsedArguments (in {args})")

        self._contexts[context] = args

    def __contains__(self, item):
        return item in self._parsed and self._parsed[item]

    def __len__(self) -> int:
        return len(self._parsed)

    def __setitem__(self, key, value):
        if isinstance(value, (collections.UserList, list)):
            true_val = [Argument(key, v, idx) for idx, v in enumerate(value)]
        else:
            true_val = [Argument(key, value, 0)]
        self._parsed[key] = true_val
        if match := EPHEMERAL_ARG_RE.fullmatch(key):
            arg, num = match.group(1), match.group(2)
            ephem_val = [EphemeralArgument(arg, v.value, v.pos, int(num)) for v in true_val]
            self._parsed[arg] = ephem_val

    def __delitem__(self, arg):
        if arg in self._parsed:
            del self._parsed[arg]

    def __iter__(self) -> Iterator[str]:
        return iter(self._parsed.keys())

    def __repr__(self):
        return f"<ParsedArguments parsed={dict.__repr__(self._parsed)} context={self._current_context}>"


# ==== other helpers ====
def argquote(arg: str):
    if any(char in arg for char in string.whitespace):
        arg = arg.replace('"', '\\"')
        arg = f'"{arg}"'
    return arg


QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "‘": "’",
    "‚": "‛",
    "“": "”",
    "„": "‟",
    "⹂": "⹂",
    "「": "」",
    "『": "』",
    "〝": "〞",
    "﹁": "﹂",
    "﹃": "﹄",
    "＂": "＂",
    "｢": "｣",
    "«": "»",
    "‹": "›",
    "《": "》",
    "〈": "〉",
}
ALL_QUOTES = set(QUOTE_PAIRS.keys()) | set(QUOTE_PAIRS.values())


class CustomStringView(StringView):
    def get_quoted_word(self):
        current = self.current
        if current is None:
            return None

        close_quote = QUOTE_PAIRS.get(current)
        is_quoted = bool(close_quote)
        if is_quoted:
            result = []
            _escaped_quotes = (current, close_quote)
        else:
            result = [current]
            _escaped_quotes = ALL_QUOTES

        while not self.eof:
            current = self.get()
            if not current:
                if is_quoted:
                    raise ExpectedClosingQuoteError(close_quote)
                return "".join(result)

            if current == "\\":
                next_char = self.get()
                if next_char in _escaped_quotes:
                    result.append(next_char)
                else:
                    self.undo()
                    result.append(current)
                continue

            if not is_quoted and current in ALL_QUOTES and current not in {"'", "’"}:
                close_quote = QUOTE_PAIRS.get(current)
                is_quoted = True
                _escaped_quotes = (current, close_quote)
                continue

            if is_quoted and current == close_quote:
                next_char = self.get()
                valid_eof = not next_char or next_char.isspace()
                if not valid_eof:
                    self.undo()
                    close_quote = None
                    is_quoted = False
                    _escaped_quotes = ALL_QUOTES
                    continue
                return "".join(result)

            if current.isspace() and not is_quoted:
                return "".join(result)

            result.append(current)


if __name__ == "__main__":  # pragma: no cover
    while True:
        try:
            print(argsplit(input(">>> ")))
        except BadArgument as e:
            print(e)
