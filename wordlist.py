"""Smart Minecraft username generation with a coolness scoring system.

This module produces candidate usernames and ranks them with a heuristic
"coolness" score (0-100). Candidates are yielded in score-descending order so
that the most desirable names are checked first. To avoid emitting long runs of
structurally similar names (which tends to trip Mojang's rate limiter), names
that share the same score are shuffled within their score band before being
yielded.

The word list and phonetic data are embedded directly in this module on
purpose: the project must not depend on any external word-list files or paid
services.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass
from itertools import product
from typing import Dict, Iterator, List, Set

# --------------------------------------------------------------------------- #
# Character classes
# --------------------------------------------------------------------------- #

VOWELS: Set[str] = set("aeiou")
CONSONANTS: Set[str] = set(string.ascii_lowercase) - VOWELS
DIGITS: Set[str] = set(string.digits)

# Mojang permits [A-Za-z0-9_] for Java usernames. We generate lowercase only
# because availability is case-insensitive and lowercase reads cleaner.
NAME_ALPHABET: str = string.ascii_lowercase + string.digits + "_"

# --------------------------------------------------------------------------- #
# Embedded data sets
# --------------------------------------------------------------------------- #

# Curated common English words (length 3-4). Kept short and "brandable" on
# purpose; obscure dictionary entries make poor usernames.
COMMON_WORDS: Set[str] = {
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

# Japanese romaji syllables / short words with a strong "cool" connotation in
# gaming culture. Kept transliterated; matched as a substring or whole token.
JAPANESE_SOUNDS: Set[str] = {
    "kai", "ryu", "ren", "kaze", "yumi", "ame", "rin", "sora", "yuki", "hana",
    "kuro", "shiro", "aka", "tora", "tsuki", "hoshi", "mai", "neko", "inu",
    "kage", "kami", "rei", "aki", "haru", "natsu", "fuyu", "mizu", "hono",
    "ken", "jin", "sho", "ryo", "tai", "yui", "nao", "emi", "ito", "oni",
}

# Very small profanity / offensive blocklist. Production deployments should
# extend this; we keep it deliberately minimal and non-exhaustive here so the
# showcase repo stays clean while still demonstrating the filter hook.
OFFENSIVE_SUBSTRINGS: Set[str] = {
    "nazi", "rape", "fuck", "shit", "cunt", "fag", "kkk", "n1gg", "nigg",
}

# --------------------------------------------------------------------------- #
# Scoring weights (no magic numbers inline elsewhere)
# --------------------------------------------------------------------------- #

SCORE_REAL_WORD: int = 40
SCORE_PRONOUNCEABLE: int = 30
SCORE_JAPANESE_VIBE: int = 20
SCORE_CLEAN_MIX: int = 15
SCORE_NO_REPEATS: int = 10

PENALTY_UNDERSCORE: int = -20
PENALTY_ALL_NUMBER: int = -30

SCORE_MIN: int = 0
SCORE_MAX: int = 100

# A pronounceable name should not stack 3+ consonants or vowels in a row.
MAX_RUN_LENGTH: int = 2

# "Clean mix" rewards names that contain both letters and digits without being
# digit-dominated. We require at least one of each and a digit ratio under this.
MAX_DIGIT_RATIO_FOR_CLEAN_MIX: float = 0.5


@dataclass(frozen=True, slots=True)
class NameCandidate:
    """An immutable scored username candidate.

    Attributes:
        name: The lowercase candidate username.
        score: Coolness score clamped to ``[SCORE_MIN, SCORE_MAX]``.
        length: Convenience copy of ``len(name)``.
    """

    name: str
    score: int
    length: int


def _has_long_run(name: str) -> bool:
    """Return ``True`` if the name has a run of same-class chars that is too long.

    A "class" here is vowel vs. consonant. Three or more consecutive vowels or
    consonants reads as unpronounceable (e.g. ``"bcd"`` or ``"aei"``).

    Args:
        name: Lowercase candidate name.

    Returns:
        Whether any same-class run exceeds :data:`MAX_RUN_LENGTH`.
    """
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
    """Heuristically decide whether a name is comfortably pronounceable.

    The heuristic: the name must contain at least one vowel and must not
    contain an overly long consonant/vowel run.

    Args:
        name: Lowercase candidate name.

    Returns:
        ``True`` if the name looks pronounceable.
    """
    has_vowel = any(char in VOWELS for char in name)
    return has_vowel and not _has_long_run(name)


def _looks_japanese(name: str) -> bool:
    """Return ``True`` if the name carries a Japanese-romaji vibe.

    We match either the whole name or a contained syllable so that names like
    ``"kai9"`` or ``"xryu"`` still earn the bonus.

    Args:
        name: Lowercase candidate name.

    Returns:
        Whether a known romaji sound is present.
    """
    if name in JAPANESE_SOUNDS:
        return True
    return any(sound in name for sound in JAPANESE_SOUNDS if len(sound) >= 3)


def _is_clean_mix(name: str) -> bool:
    """Return ``True`` for a balanced alphanumeric mix.

    A clean mix has at least one letter and one digit, and digits do not
    dominate the name.

    Args:
        name: Lowercase candidate name.

    Returns:
        Whether the name is a balanced letter/digit mix.
    """
    digit_count = sum(char in DIGITS for char in name)
    letter_count = sum(char.isalpha() for char in name)
    if digit_count == 0 or letter_count == 0:
        return False
    return digit_count / len(name) < MAX_DIGIT_RATIO_FOR_CLEAN_MIX


def is_skippable(name: str) -> bool:
    """Return ``True`` if a name should never be generated or checked.

    Skip rules:
        * Empty names.
        * All characters identical (e.g. ``"aaa"``, ``"111"``).
        * Leading or trailing underscore.
        * Contains a blocked offensive substring.

    Args:
        name: Lowercase candidate name.

    Returns:
        Whether the name should be discarded outright.
    """
    if not name:
        return True
    if len(set(name)) == 1:
        return True
    if name.startswith("_") or name.endswith("_"):
        return True
    return any(bad in name for bad in OFFENSIVE_SUBSTRINGS)


def score_name(name: str) -> int:
    """Compute the coolness score for a single username.

    The score is the sum of the criteria below, clamped to
    ``[SCORE_MIN, SCORE_MAX]``:

        * Real English word match: ``+40``
        * Pronounceable pattern: ``+30``
        * Japanese romaji vibe: ``+20``
        * Clean alphanumeric mix: ``+15``
        * No repeated characters: ``+10``
        * Contains an underscore: ``-20``
        * All digits: ``-30``

    Args:
        name: Lowercase candidate name.

    Returns:
        Clamped integer score in ``[0, 100]``.
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

    if "_" in name:
        score += PENALTY_UNDERSCORE
    if name.isdigit():
        score += PENALTY_ALL_NUMBER

    # Clamp so downstream code can rely on a stable 0-100 range.
    return max(SCORE_MIN, min(SCORE_MAX, score))


def _bucket_by_score(lengths: List[int]) -> Dict[int, List[str]]:
    """Generate every candidate for the requested lengths, bucketed by score.

    Args:
        lengths: Username lengths to enumerate (e.g. ``[3, 4]``).

    Returns:
        Mapping of score to the list of names that earned that score. Skippable
        names are excluded entirely.
    """
    buckets: Dict[int, List[str]] = {}
    for length in lengths:
        # itertools.product over the alphabet is the full keyspace; for length
        # 3-4 this is well within memory budget and lets us rank globally.
        for chars in product(NAME_ALPHABET, repeat=length):
            name = "".join(chars)
            if is_skippable(name):
                continue
            buckets.setdefault(score_name(name), []).append(name)
    return buckets


def generate_candidates(
    lengths: List[int],
    *,
    seed: int | None = None,
) -> Iterator[NameCandidate]:
    """Yield scored candidates best-first, shuffled within each score band.

    Names are enumerated for every requested length, scored, grouped by score,
    and yielded from the highest score downward. Within a single score band the
    order is randomized so that consecutive requests do not follow an obvious
    lexical pattern (which makes rate limiting more aggressive).

    Args:
        lengths: Username lengths to enumerate (e.g. ``[3, 4]``).
        seed: Optional RNG seed for reproducible ordering in tests.

    Yields:
        :class:`NameCandidate` objects in score-descending order.
    """
    rng = random.Random(seed)
    buckets = _bucket_by_score(lengths)

    for score in sorted(buckets, reverse=True):
        band = buckets[score]
        rng.shuffle(band)
        for name in band:
            yield NameCandidate(name=name, score=score, length=len(name))
