"""Username generation + a rough "coolness" score.

Generates every candidate of the requested length(s), scores each one 0-100
with a few cheap heuristics, and yields them best-first. Names that tie on
score get shuffled inside their band - long runs of near-identical names make
Mojang's rate limiter twitchy.

Everything (word list, romaji) is baked into this file on purpose so the tool
has zero external data dependencies.
"""

import random
import string
from dataclasses import dataclass
from itertools import product

VOWELS = set("aeiou")
CONSONANTS = set(string.ascii_lowercase) - VOWELS
DIGITS = set(string.digits)

# Mojang allows [A-Za-z0-9_]. Lowercase only - availability isn't case
# sensitive and lowercase just looks better.
NAME_ALPHABET = string.ascii_lowercase + string.digits + "_"

# Short, brandable 3-4 letter words. Obscure dictionary words make bad
# usernames so they're left out.
COMMON_WORDS = {
    # 3-letter
    "ace", "air", "art", "ash", "axe", "bat", "bee", "big", "bit", "box",
    "bug", "cat", "cub", "cup", "dao", "day", "dot", "dry", "ego", "elf",
    "fox", "fun", "gem", "god", "gum", "hit", "ice", "ink", "ion", "ivy",
    "jam", "jaw", "jet", "joy", "key", "kid", "kit", "lab", "law", "log",
    "map", "max", "mix", "mob", "neo", "net", "nova", "oak", "orb", "owl",
    "pen", "pet", "pie", "pin", "pop", "pro", "pub", "ray", "red", "rib",
    "rim", "rip", "run", "saw", "sea", "sky", "spy", "sun", "tag", "tea",
    "tip", "toe", "top", "toy", "vex", "war", "wax", "web", "win", "wit",
    "yes", "zap", "zen", "zip", "zoo",
    # 4-letter
    "ally", "apex", "aqua", "aura", "beam", "bolt", "byte", "calm", "cell",
    "chip", "city", "claw", "clay", "code", "cold", "core", "dawn", "deck",
    "doom", "dusk", "echo", "edge", "epic", "fang", "fire", "flux", "foxy",
    "fury", "gold", "halo", "hawk", "haze", "hero", "hive", "iron", "jade",
    "jolt", "kite", "lava", "leaf", "lion", "lord", "luck", "luna", "lynx",
    "mage", "mint", "moon", "moss", "muse", "neon", "node", "nova", "onyx",
    "opal", "orca", "peak", "plot", "puma", "pyro", "rage", "rain",
    "rift", "ruby", "rune", "rush", "sage", "salt", "scar", "seed", "ship",
    "snow", "soul", "star", "tide", "tofu", "vibe", "void", "volt", "ward",
    "wave", "wing", "wolf", "yarn", "zero", "zeta", "zone",
}

# Romaji syllables / short words that read as "cool" in gaming circles.
# Matched whole or as a substring.
JAPANESE_SOUNDS = {
    "kai", "ryu", "ren", "kaze", "yumi", "ame", "rin", "sora", "yuki", "hana",
    "kuro", "shiro", "aka", "tora", "tsuki", "hoshi", "mai", "neko", "inu",
    "kage", "kami", "rei", "aki", "haru", "natsu", "fuyu", "mizu", "hono",
    "ken", "jin", "sho", "ryo", "tai", "yui", "nao", "emi", "ito", "oni",
}

# Tiny blocklist. Deliberately not exhaustive - extend it if you actually
# deploy this somewhere.
OFFENSIVE_SUBSTRINGS = {
    "nazi", "rape", "fuck", "shit", "cunt", "fag", "kkk", "n1gg", "nigg",
}

SCORE_REAL_WORD = 40
SCORE_PRONOUNCEABLE = 30
SCORE_JAPANESE_VIBE = 20
SCORE_CLEAN_MIX = 15
SCORE_NO_REPEATS = 10

PENALTY_ALL_NUMBER = -30

SCORE_MIN = 0
SCORE_MAX = 100

# Anything longer than this many same-class chars in a row reads as a mouthful.
MAX_RUN_LENGTH = 2

# For the "clean mix" bonus: need letters and digits, but digits can't be more
# than half the name.
MAX_DIGIT_RATIO_FOR_CLEAN_MIX = 0.5


@dataclass(frozen=True, slots=True)
class NameCandidate:
    name: str
    score: int
    length: int


def _has_long_run(name: str) -> bool:
    # "class" = vowel vs consonant. 3+ vowels or 3+ consonants in a row
    # ("aei", "bcd") is hard to say.
    run = 1
    for prev, current in zip(name, name[1:]):
        same_class = (prev in VOWELS) == (current in VOWELS)
        letters_only = prev.isalpha() and current.isalpha()
        if same_class and letters_only:
            run += 1
            if run > MAX_RUN_LENGTH:
                return True
        else:
            run = 1
    return False


def _is_pronounceable(name: str) -> bool:
    # Needs at least one vowel and no awkward long run.
    has_vowel = any(char in VOWELS for char in name)
    return has_vowel and not _has_long_run(name)


def _looks_japanese(name: str) -> bool:
    # Whole match, or contains a 3+ char syllable, so "kai9" / "xryu" still
    # count.
    if name in JAPANESE_SOUNDS:
        return True
    return any(sound in name for sound in JAPANESE_SOUNDS if len(sound) >= 3)


def _is_clean_mix(name: str) -> bool:
    digit_count = sum(char in DIGITS for char in name)
    letter_count = sum(char.isalpha() for char in name)
    if digit_count == 0 or letter_count == 0:
        return False
    return digit_count / len(name) < MAX_DIGIT_RATIO_FOR_CLEAN_MIX


def is_skippable(name: str) -> bool:
    """Names we never bother generating or checking.

    Empty, all-same-char (aaa / 111), starts/ends with _, or contains
    something on the blocklist.
    """
    if not name:
        return True
    if len(set(name)) == 1:
        return True
    if name.startswith("_") or name.endswith("_"):
        return True
    return any(bad in name for bad in OFFENSIVE_SUBSTRINGS)


def score_name(name: str) -> int:
    """Sum the bonuses/penalties below and clamp to [0, 100].

    +40 real word, +30 pronounceable, +20 romaji vibe, +15 clean mix,
    +10 no repeated chars, -30 all digits.
    """
    score = 0

    if name in COMMON_WORDS:
        score += SCORE_REAL_WORD
    if _is_pronounceable(name):
        score += SCORE_PRONOUNCEABLE
    if _looks_japanese(name):
        score += SCORE_JAPANESE_VIBE
    if _is_clean_mix(name):
        score += SCORE_CLEAN_MIX
    if len(set(name)) == len(name):
        score += SCORE_NO_REPEATS

    # No underscore penalty: is_skippable() already rejects names that
    # start or end with "_", so scoring them would be dead code.
    if name.isdigit():
        score += PENALTY_ALL_NUMBER

    return max(SCORE_MIN, min(SCORE_MAX, score))


def _bucket_by_score(lengths):
    buckets = {}
    for length in lengths:
        # Full keyspace via itertools.product. For length 3-4 this fits in
        # memory fine and lets us rank everything globally.
        for chars in product(NAME_ALPHABET, repeat=length):
            name = "".join(chars)
            if is_skippable(name):
                continue
            buckets.setdefault(score_name(name), []).append(name)
    return buckets


def generate_candidates(lengths, *, seed=None):
    """Yield NameCandidate objects high score first.

    Within one score the order is randomized (optionally seeded for tests) so
    consecutive requests aren't an obvious lexical march, which the rate
    limiter punishes.
    """
    rng = random.Random(seed)
    buckets = _bucket_by_score(lengths)

    for score in sorted(buckets, reverse=True):
        band = buckets[score]
        rng.shuffle(band)
        for name in band:
            yield NameCandidate(name=name, score=score, length=len(name))
