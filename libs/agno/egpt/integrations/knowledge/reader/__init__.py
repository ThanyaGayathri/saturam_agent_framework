"""Concrete readers (EGPT-1.8).

Deliberately bare. Each reader carries an optional dependency -- pypdf, python-docx,
firecrawl, docling and so on -- so re-exporting them here would make
``import egpt.integrations.knowledge.reader`` demand every one of them. Import the reader
you want by its module path, or resolve it through ``ReaderFactory`` in core, which is the
construction seam and imports lazily.

The ``Reader`` interface stays in core at ``egpt.knowledge.reader.base``.
"""
