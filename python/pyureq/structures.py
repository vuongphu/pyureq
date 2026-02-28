"""Data structures used by pyureq."""

from collections.abc import MutableMapping


class CaseInsensitiveDict(MutableMapping):
    """A case-insensitive ``dict``-like object.

    Implements all methods and operations of ``MutableMapping`` as well as
    dict's ``copy``. Also provides ``lower_items``.

    All keys are expected to be strings. The structure remembers the last
    case a key was set with, which is convenient for round-tripping HTTP
    headers.

    Example::

        >>> d = CaseInsensitiveDict()
        >>> d['Accept'] = 'application/json'
        >>> d['accept'] == 'application/json'
        True
        >>> list(d.keys())
        ['Accept']
    """

    def __init__(self, data=None, **kwargs):
        # _store maps lower-cased key -> (original_key, value)
        self._store: dict = {}
        if data is None:
            data = {}
        self.update(data, **kwargs)

    def __setitem__(self, key, value):
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key):
        return self._store[key.lower()][1]

    def __delitem__(self, key):
        del self._store[key.lower()]

    def __iter__(self):
        return (origkey for origkey, _ in self._store.values())

    def __len__(self):
        return len(self._store)

    def __contains__(self, key):
        return key.lower() in self._store

    def lower_items(self):
        """Like ``items()``, but with lowercased keys."""
        return ((lk, v) for lk, (_, v) in self._store.items())

    def __eq__(self, other):
        if isinstance(other, CaseInsensitiveDict):
            return dict(self.lower_items()) == dict(other.lower_items())
        return dict(self.lower_items()) == {k.lower(): v for k, v in dict(other).items()}

    def copy(self):
        return CaseInsensitiveDict(self._store.values())

    def __repr__(self):
        return str(dict(self.items()))
