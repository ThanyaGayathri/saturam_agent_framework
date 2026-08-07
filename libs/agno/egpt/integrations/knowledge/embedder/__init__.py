"""Concrete embedders (EGPT-1.8).

Deliberately bare, for the same reason as the reader package: every embedder here is a
vendor client behind an optional SDK, so re-exporting them would make importing this package
require all of them at once. Import the embedder you want by its module path.

The ``Embedder`` interface stays in core at ``egpt.knowledge.embedder.base``.
"""
