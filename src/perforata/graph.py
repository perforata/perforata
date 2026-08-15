"""Execution graph: Node base class, linear Pipeline, and full DAG Graph.

Nodes are small, well-posed processing units. Data (usually a
:class:`~perforata.pointcloud.PointCloud` or a list of shape dicts)
flows between them.

Two composition styles are offered:

* :class:`Pipeline` — a simple linear chain (covers the "simple UI" case:
  generator -> modifiers -> decorator -> ops -> export).
* :class:`Graph` — a named DAG for advanced compositions (multiple
  generators merged, one field modulating several attributes, etc.).
"""

from __future__ import annotations


class Node:
    """Base class for all processing nodes.

    Subclasses implement :meth:`run`. Parameters are stored in
    ``self.params`` so UIs can introspect and serialize any node.
    """

    def __init__(self, **params):
        self.params = params

    def run(self, *inputs):
        raise NotImplementedError

    def __call__(self, *inputs):
        return self.run(*inputs)

    def __repr__(self):
        kv = ", ".join(f"{k}={v!r}" for k, v in self.params.items())
        return f"{type(self).__name__}({kv})"


class Pipeline(Node):
    """A linear chain of nodes.

    The first node is called with no input (typically a generator);
    each subsequent node receives the previous node's output.
    """

    def __init__(self, *nodes):
        super().__init__()
        self.nodes = list(nodes)

    def add(self, node: Node) -> "Pipeline":
        self.nodes.append(node)
        return self

    def run(self, data=None):
        for node in self.nodes:
            data = node() if data is None else node(data)
        return data

    def __repr__(self):
        inner = " -> ".join(repr(n) for n in self.nodes)
        return f"Pipeline({inner})"


class Graph:
    """A named DAG of nodes for advanced compositions.

    Example::

        g = Graph()
        g.add("grid", CartesianGrid(nx=20, ny=20, pitch=10))
        g.add("logo", ImageField("logo.png"))
        g.add("mod", FieldModulate(attr="scale"), "grid", "logo")
        g.add("cuts", ShapeInstancer(rules={...}), "mod")
        shapes = g.run("cuts")
    """

    def __init__(self):
        self._nodes: dict[str, tuple[Node, tuple[str, ...]]] = {}

    def add(self, name: str, node: Node, *inputs: str) -> str:
        """Register ``node`` under ``name``; ``inputs`` are names of
        upstream nodes whose outputs become positional arguments."""
        if name in self._nodes:
            raise ValueError(f"node {name!r} already exists")
        for dep in inputs:
            if dep not in self._nodes:
                raise ValueError(f"unknown input node {dep!r} for {name!r}")
        self._nodes[name] = (node, inputs)
        return name

    def run(self, output: str):
        """Evaluate the graph and return the named node's output.
        Results are memoized so shared upstream nodes run once."""
        cache: dict[str, object] = {}
        visiting: set[str] = set()

        def _eval(name: str):
            if name in cache:
                return cache[name]
            if name in visiting:
                raise ValueError(f"cycle detected at node {name!r}")
            if name not in self._nodes:
                raise KeyError(f"unknown node {name!r}")
            visiting.add(name)
            node, inputs = self._nodes[name]
            args = [_eval(dep) for dep in inputs]
            result = node(*args)
            visiting.discard(name)
            cache[name] = result
            return result

        return _eval(output)

    def __repr__(self):
        lines = [f"  {name} <- {list(inputs)} : {node!r}"
                 for name, (node, inputs) in self._nodes.items()]
        return "Graph(\n" + "\n".join(lines) + "\n)"
