"""Tree of all files in a backup (for the file browser)."""

from __future__ import annotations

from .reader import Entry

GROUP_ORDER = ["Camera Roll", "Media", "Apps", "App Groups", "App Extensions", "System"]

_SPECIAL = {
    "CameraRollDomain": ("Camera Roll", None),
    "MediaDomain": ("Media", None),
    "HomeDomain": ("Home", "System"),
}


def domain_location(domain: str) -> tuple[str, str | None]:
    """(display name, group) of a domain. Group ``None`` = top level."""
    if domain in _SPECIAL:
        return _SPECIAL[domain]
    for prefix, group in (("AppDomainGroup-", "App Groups"),
                          ("AppDomainPlugin-", "App Extensions"),
                          ("AppDomain-", "Apps")):
        if domain.startswith(prefix):
            return domain[len(prefix):], group
    return domain, "System"


class Node:
    __slots__ = ("name", "parent", "children", "entry", "_index", "row")

    def __init__(self, name: str, parent: "Node | None" = None, entry: Entry | None = None):
        self.name = name
        self.parent = parent
        self.children: list[Node] = []
        self.entry = entry
        self._index: dict[str, Node] | None = None
        self.row = 0

    @property
    def is_dir(self) -> bool:
        return self.entry is None or self.entry.is_dir

    def child(self, name: str) -> "Node":
        if self._index is None:
            self._index = {}
        node = self._index.get(name)
        if node is None:
            node = Node(name, self)
            self._index[name] = node
            self.children.append(node)
        return node

    def path(self) -> str:
        parts = []
        node = self
        while node is not None and node.parent is not None:
            parts.append(node.name)
            node = node.parent
        return "/".join(reversed(parts))

    def iter_files(self):
        stack = [self]
        while stack:
            n = stack.pop()
            if n.entry is not None and n.entry.is_file:
                yield n
            stack.extend(n.children)

    def finalize(self) -> None:
        stack = [self]
        while stack:
            n = stack.pop()
            if n.parent is None:
                order = {g: i for i, g in enumerate(GROUP_ORDER)}
                n.children.sort(key=lambda c: (order.get(c.name, 99), c.name.lower()))
            else:
                n.children.sort(key=lambda c: (not c.is_dir, c.name.lower()))
            for i, c in enumerate(n.children):
                c.row = i
            n._index = None
            stack.extend(n.children)


def build_tree(entries: list[Entry]) -> Node:
    root = Node("")
    for e in entries:
        if e.flags not in (1, 2, 4):
            continue
        label, group = domain_location(e.domain)
        node = root.child(group) if group else root
        node = node.child(label)
        if e.path:
            for part in e.path.split("/"):
                if part:
                    node = node.child(part)
            if node.entry is None or e.is_file:
                node.entry = e
    root.finalize()
    return root
