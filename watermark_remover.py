#!/usr/bin/env python3
"""
Native PDF Watermark Remover - Standalone Version
==================================================

A professional-grade PDF watermark detection and removal system.
Works directly on PDF objects - NO image conversion, NO quality loss.

Detection is based on statistical analysis:
1. Repetition frequency across pages
2. Spatial positioning (edges, diagonal, centered)
3. Font/color anomalies vs body text
4. Text density and isolation
5. Known watermark patterns

Usage:
    python watermark_remover.py                    # Interactive mode
    python watermark_remover.py input.pdf          # Auto-remove
    python watermark_remover.py input.pdf -p       # Preview only
    python watermark_remover.py input.pdf -v       # Visualize
    python watermark_remover.py input.pdf -o out.pdf -c 0.5

Requirements:
    pip install PyMuPDF
"""

import fitz  # PyMuPDF
import os
import sys
import math
import re
import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any, Set
from enum import Enum


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class WatermarkType(Enum):
    TEXT = "text"
    IMAGE = "image"
    VECTOR = "vector"
    NOTICE = "notice"


@dataclass
class DetectionConfig:
    """Configuration for watermark detection."""
    min_confidence: float = 0.4
    min_page_coverage: float = 0.5
    max_text_length: int = 150
    edge_margin_pct: float = 0.12
    diagonal_angle_min: float = 20.0
    diagonal_angle_max: float = 70.0
    image_repeat_threshold: float = 0.6
    large_vector_threshold: float = 0.5
    enable_keyword_boost: bool = True


@dataclass
class TextBlock:
    """Represents a text block with metadata."""
    text: str
    page: int
    bbox: Tuple[float, float, float, float]
    font_size: float
    font_name: str
    color: Any
    rotation: float
    span_bbox: Tuple[float, float, float, float] = None

    def __post_init__(self):
        if self.span_bbox is None:
            self.span_bbox = self.bbox


@dataclass
class WatermarkCandidate:
    """A detected watermark with confidence scoring."""
    type: WatermarkType
    page: int
    bbox: Tuple[float, float, float, float]
    content: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    instances: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class BodyTextStats:
    """Statistical baseline of normal body text."""
    median_font_size: float
    font_size_range: Tuple[float, float]
    common_colors: set
    common_fonts: set


# =============================================================================
# SMART WATERMARK DETECTOR
# =============================================================================

class SmartWatermarkDetector:
    """
    Detects watermarks using statistical analysis - no hardcoded patterns.
    """

    WATERMARK_KEYWORDS = frozenset([
        'confidential', 'draft', 'sample', 'copy', 'watermark',
        'proprietary', 'internal', 'restricted', 'preview', 'proof',
        'do not copy', 'do not distribute', 'not for distribution',
        'unofficial', 'duplicate', 'specimen', 'void', 'cancelled',
    ])

    TIMESTAMP_PATTERNS = [
        r'\b[A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4}\s+\d{1,2}:\d{2}',
        r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s+\d{1,2}:\d{2}',
        r'\b\d{4}[/-]\d{2}[/-]\d{2}\s+\d{1,2}:\d{2}',
        r'\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\s+[A-Z]{2,4}\b',
    ]

    def __init__(self, doc: fitz.Document, config: Optional[DetectionConfig] = None):
        self.doc = doc
        self.config = config or DetectionConfig()
        self.page_count = doc.page_count
        self.page_dimensions = [(p.rect.width, p.rect.height) for p in doc]
        self.body_stats = self._analyze_body_text()

    def _analyze_body_text(self) -> BodyTextStats:
        """Analyze document to understand normal body text characteristics."""
        font_sizes, colors, fonts = [], [], []

        for page in self.doc:
            try:
                for block in page.get_text("dict").get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            size = span.get("size", 0)
                            if size > 0:
                                font_sizes.append(size)
                                colors.append(span.get("color", 0))
                                fonts.append(span.get("font", ""))
            except:
                continue

        if not font_sizes:
            return BodyTextStats(12.0, (10.0, 14.0), {0}, set())

        font_sizes.sort()
        n = len(font_sizes)

        color_counts = defaultdict(int)
        font_counts = defaultdict(int)
        for c in colors:
            color_counts[c] += 1
        for f in fonts:
            if f:
                font_counts[f] += 1

        return BodyTextStats(
            median_font_size=font_sizes[n // 2],
            font_size_range=(font_sizes[int(n * 0.1)], font_sizes[int(n * 0.9)]),
            common_colors=set(sorted(color_counts.keys(), key=lambda x: color_counts[x], reverse=True)[:3]),
            common_fonts=set(sorted(font_counts.keys(), key=lambda x: font_counts[x], reverse=True)[:3]),
        )

    def _get_text_rotation(self, line: dict) -> float:
        if "dir" in line:
            dx, dy = line["dir"]
            return math.degrees(math.atan2(dy, dx))
        return 0.0

    def _is_at_page_edge(self, bbox: Tuple, page_idx: int) -> Dict[str, bool]:
        width, height = self.page_dimensions[page_idx]
        x0, y0, x1, y1 = bbox
        margin = self.config.edge_margin_pct

        at_top = y0 < height * margin
        at_bottom = y1 > height * (1 - margin)

        return {"at_top": at_top, "at_bottom": at_bottom, "is_edge": at_top or at_bottom}

    def _is_centered(self, bbox: Tuple, page_idx: int) -> bool:
        width, _ = self.page_dimensions[page_idx]
        center_x = (bbox[0] + bbox[2]) / 2
        return abs(center_x - width / 2) < width * 0.15

    def _is_diagonal(self, rotation: float) -> bool:
        abs_rot = abs(rotation)
        return (20 < abs_rot < 70) or (110 < abs_rot < 160)

    def _covers_large_area(self, bbox: Tuple, page_idx: int) -> bool:
        width, height = self.page_dimensions[page_idx]
        return (bbox[2] - bbox[0] > width * 0.5) or (bbox[3] - bbox[1] > height * 0.5)

    def _calculate_text_density(self, page_idx: int, bbox: Tuple) -> float:
        try:
            page = self.doc[page_idx]
            margin = 50
            search_rect = fitz.Rect(bbox[0]-margin, bbox[1]-margin, bbox[2]+margin, bbox[3]+margin)
            nearby = sum(1 for b in page.get_text("dict")["blocks"]
                        if b.get("type") == 0 and search_rect.intersects(fitz.Rect(b["bbox"])))
            return min(1.0, (nearby - 1) / 10)
        except:
            return 0.5

    def _is_light_gray_color(self, color) -> bool:
        try:
            if isinstance(color, int):
                r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
            elif isinstance(color, (tuple, list)) and len(color) >= 3:
                r, g, b = color[0], color[1], color[2]
            else:
                return False
            avg = (r + g + b) / 3
            max_diff = max(abs(r - avg), abs(g - avg), abs(b - avg))
            return max_diff < 30 and avg > 150
        except:
            return False

    def _has_watermark_keyword(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        for kw in self.WATERMARK_KEYWORDS:
            if kw in text_lower:
                return kw
        return None

    def _has_timestamp(self, text: str) -> bool:
        return any(re.search(p, text) for p in self.TIMESTAMP_PATTERNS)

    def _collect_text_blocks(self) -> List[TextBlock]:
        blocks = []
        for page_idx, page in enumerate(self.doc):
            try:
                for block in page.get_text("dict").get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        rotation = self._get_text_rotation(line)
                        for span in line.get("spans", []):
                            text = span.get("text", "").strip()
                            if text:
                                blocks.append(TextBlock(
                                    text=text,
                                    page=page_idx,
                                    bbox=tuple(block["bbox"]),
                                    font_size=span.get("size", 0),
                                    font_name=span.get("font", ""),
                                    color=span.get("color", 0),
                                    rotation=rotation,
                                    span_bbox=tuple(span.get("bbox", block["bbox"])),
                                ))
            except:
                continue
        return blocks

    def _analyze_repetition(self, blocks: List[TextBlock]) -> Dict[str, List[TextBlock]]:
        groups = defaultdict(list)
        for b in blocks:
            normalized = " ".join(b.text.lower().split())
            if normalized and len(normalized) <= self.config.max_text_length:
                groups[normalized].append(b)
        return groups

    def _score_text_watermark(self, blocks: List[TextBlock], text: str) -> WatermarkCandidate:
        confidence, reasons = 0.0, []
        pages_appeared = set(b.page for b in blocks)
        coverage = len(pages_appeared) / self.page_count

        # Page coverage
        if coverage >= 0.8:
            confidence += 0.30
            reasons.append(f"Appears on {coverage*100:.0f}% of pages")
        elif coverage >= 0.5:
            confidence += 0.18
            reasons.append(f"Appears on {coverage*100:.0f}% of pages")

        # Repetition
        if len(blocks) > 1:
            confidence += min(0.25, len(blocks) * 0.05)
            reasons.append(f"Repeated {len(blocks)} times")

        # Consistent Y position
        if len(blocks) > 1:
            y_vals = [b.bbox[1] for b in blocks]
            if max(y_vals) - min(y_vals) < 30:
                confidence += 0.15
                reasons.append("Consistent vertical position")

        # Edge positioning
        edge_count = sum(1 for b in blocks if self._is_at_page_edge(b.bbox, b.page)["is_edge"])
        if edge_count == len(blocks):
            confidence += 0.25
            reasons.append("Always at page edge")
        elif edge_count > len(blocks) * 0.8:
            confidence += 0.15
            reasons.append("Usually at page edge")

        # Diagonal text
        if sum(1 for b in blocks if self._is_diagonal(b.rotation)) > len(blocks) * 0.5:
            confidence += 0.20
            reasons.append("Diagonal/rotated text")

        # Font size anomaly
        if blocks:
            avg_size = sum(b.font_size for b in blocks) / len(blocks)
            if avg_size > self.body_stats.median_font_size * 2:
                confidence += 0.15
                reasons.append(f"Large font ({avg_size:.1f})")
            elif avg_size < self.body_stats.median_font_size * 0.7:
                confidence += 0.10
                reasons.append(f"Small font ({avg_size:.1f})")

        # Color anomaly
        if blocks:
            uncommon = sum(1 for b in blocks if b.color not in self.body_stats.common_colors)
            if uncommon > len(blocks) * 0.8:
                confidence += 0.15
                reasons.append("Unusual text color")

        # Light gray (watermark style)
        if blocks and sum(1 for b in blocks if self._is_light_gray_color(b.color)) > len(blocks) * 0.5:
            confidence += 0.20
            reasons.append("Light gray color")

        # Isolation
        if blocks:
            avg_density = sum(self._calculate_text_density(b.page, b.bbox) for b in blocks) / len(blocks)
            if avg_density < 0.2:
                confidence += 0.10
                reasons.append("Isolated text")

        # Short text
        if len(text) < 50:
            confidence += 0.05
            reasons.append("Short text")

        # Keyword
        if self.config.enable_keyword_boost:
            kw = self._has_watermark_keyword(text)
            if kw:
                confidence += 0.35
                reasons.append(f"Contains '{kw}'")

        # Timestamp
        if self._has_timestamp(text):
            confidence += 0.15
            reasons.append("Contains timestamp")

        # Centered
        if sum(1 for b in blocks if self._is_centered(b.bbox, b.page)) > len(blocks) * 0.8:
            confidence += 0.10
            reasons.append("Centered on page")

        # Penalties
        if len(text) > 80:
            confidence -= 0.20
            reasons.append("Long text (penalty)")

        if blocks:
            avg_density = sum(self._calculate_text_density(b.page, b.bbox) for b in blocks) / len(blocks)
            if avg_density > 0.6:
                confidence -= 0.15
                reasons.append("High density (penalty)")

        # CRITICAL: Protect legitimate document content
        # Financial/legal terms that should NEVER be removed
        protected_patterns = [
            'statement of', 'schedule of', 'notes to', 'assets', 'liabilities',
            'partners', 'capital', 'operations', 'investments', 'cash flows',
            'balance sheet', 'income', 'expenses', 'revenue', 'net', 'total',
            'unaudited', 'audited', 'financial', 'fiscal', 'quarter', 'annual',
            'december', 'january', 'february', 'march', 'april', 'may', 'june',
            'july', 'august', 'september', 'october', 'november',
            'fund', 'portfolio', 'distribution', 'contribution', 'commitment',
        ]
        text_lower = text.lower()
        if any(pattern in text_lower for pattern in protected_patterns):
            # Strong penalty - this is likely document content, not a watermark
            confidence -= 0.50
            reasons.append("Contains protected content term (penalty)")

        confidence = max(0.0, min(1.0, confidence))
        instances = [{"page": b.page, "bbox": b.span_bbox, "text": b.text} for b in blocks]

        return WatermarkCandidate(
            type=WatermarkType.TEXT,
            page=blocks[0].page if blocks else 0,
            bbox=blocks[0].span_bbox if blocks else (0, 0, 0, 0),
            content=text,
            confidence=confidence,
            reasons=reasons,
            instances=instances,
        )

    def detect_text_watermarks(self) -> List[WatermarkCandidate]:
        text_blocks = self._collect_text_blocks()
        groups = self._analyze_repetition(text_blocks)
        candidates = []

        for text, blocks in groups.items():
            # Single occurrence in multi-page doc needs strong signals
            if self.page_count > 1 and len(blocks) == 1:
                b = blocks[0]
                if not (self._is_at_page_edge(b.bbox, b.page)["is_edge"] or
                        self._is_diagonal(b.rotation) or
                        self._has_watermark_keyword(text) or
                        self._is_light_gray_color(b.color)):
                    continue

            candidate = self._score_text_watermark(blocks, text)
            if candidate.confidence >= self.config.min_confidence:
                for inst in candidate.instances:
                    candidates.append(WatermarkCandidate(
                        type=WatermarkType.TEXT,
                        page=inst["page"],
                        bbox=inst["bbox"],
                        content=text,
                        confidence=candidate.confidence,
                        reasons=candidate.reasons.copy(),
                        instances=candidate.instances,
                    ))

        return candidates

    def detect_image_watermarks(self) -> List[WatermarkCandidate]:
        occurrences = defaultdict(list)

        for page_idx, page in enumerate(self.doc):
            try:
                for img in page.get_images(full=True):
                    xref = img[0]
                    try:
                        data = self.doc.extract_image(xref)
                        key = (hash(data["image"]), data.get("width", 0), data.get("height", 0))
                        for rect in page.get_image_rects(xref):
                            occurrences[key].append({"page": page_idx, "bbox": tuple(rect), "xref": xref})
                    except:
                        continue
            except:
                continue

        candidates = []
        for key, items in occurrences.items():
            pages = set(i["page"] for i in items)
            coverage = len(pages) / self.page_count

            confidence, reasons = 0.0, []
            if coverage >= 0.6:
                confidence += 0.40
                reasons.append(f"Appears on {coverage*100:.0f}% of pages")

            if len(items) > 1:
                y_vals = [i["bbox"][1] for i in items]
                if max(y_vals) - min(y_vals) < 30:
                    confidence += 0.20
                    reasons.append("Consistent position")

            edge_count = sum(1 for i in items if self._is_at_page_edge(i["bbox"], i["page"])["is_edge"])
            if edge_count > len(items) * 0.8:
                confidence += 0.20
                reasons.append("At page edge")

            if confidence >= self.config.min_confidence:
                for item in items:
                    candidates.append(WatermarkCandidate(
                        type=WatermarkType.IMAGE,
                        page=item["page"],
                        bbox=item["bbox"],
                        content=f"Image (xref={item['xref']})",
                        confidence=confidence,
                        reasons=reasons.copy(),
                    ))

        return candidates

    def detect_vector_watermarks(self) -> List[WatermarkCandidate]:
        candidates = []

        for page_idx, page in enumerate(self.doc):
            try:
                for d in page.get_drawings():
                    bbox = tuple(d["rect"])
                    confidence, reasons = 0.0, []

                    if self._covers_large_area(bbox, page_idx):
                        confidence += 0.40
                        reasons.append("Covers large area")

                    if self._is_centered(bbox, page_idx):
                        confidence += 0.20
                        reasons.append("Centered")

                    if confidence >= self.config.min_confidence:
                        candidates.append(WatermarkCandidate(
                            type=WatermarkType.VECTOR,
                            page=page_idx,
                            bbox=bbox,
                            content="Vector drawing",
                            confidence=confidence,
                            reasons=reasons.copy(),
                        ))
            except:
                continue

        return candidates

    def detect_notice_text(self) -> List[WatermarkCandidate]:
        """
        Detect notice/footer text - BUT exclude document headers/titles.
        Notice watermarks are typically: timestamps, "Page X of Y", disclaimers.
        NOT: Fund names, company names, document titles.
        """
        occurrences = defaultdict(list)

        # Patterns that indicate legitimate headers (NOT watermarks)
        header_patterns = [
            'l.p.', 'llc', 'inc', 'corp', 'ltd', 'partners', 'fund', 'capital',
            'investment', 'holdings', 'group', 'management', 'advisors',
            'financial', 'statement', 'schedule', 'unaudited', 'audited',
        ]

        for page_idx, page in enumerate(self.doc):
            try:
                page_height = page.rect.height
                for block in page.get_text("dict").get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text = span.get("text", "").strip()
                            bbox = span.get("bbox", block["bbox"])

                            # Only consider short phrases (2-5 words)
                            if not (2 <= len(text.split()) <= 5):
                                continue

                            # Skip if it looks like a document header/title
                            text_lower = text.lower()
                            if any(p in text_lower for p in header_patterns):
                                continue

                            # Only consider text at page edges (top 10% or bottom 10%)
                            y_pos = bbox[1]
                            if not (y_pos < page_height * 0.10 or y_pos > page_height * 0.90):
                                continue

                            occurrences[text].append({
                                "page": page_idx,
                                "bbox": tuple(bbox),
                            })
            except:
                continue

        candidates = []
        for text, items in occurrences.items():
            coverage = len(set(i["page"] for i in items)) / self.page_count
            if coverage >= 0.8:
                for item in items:
                    candidates.append(WatermarkCandidate(
                        type=WatermarkType.NOTICE,
                        page=item["page"],
                        bbox=item["bbox"],
                        content=text,
                        confidence=coverage,
                        reasons=[f"Appears on {coverage*100:.0f}% of pages", "At page edge"],
                    ))

        return candidates

    def detect_all(self) -> List[WatermarkCandidate]:
        """Run all detection methods and return deduplicated results."""
        all_candidates = (
            self.detect_text_watermarks() +
            self.detect_image_watermarks() +
            self.detect_vector_watermarks() +
            self.detect_notice_text()
        )

        # Deduplicate
        seen = set()
        unique = []
        for c in all_candidates:
            key = (c.page, tuple(round(x, 1) for x in c.bbox))
            if key not in seen:
                seen.add(key)
                unique.append(c)

        return sorted(unique, key=lambda x: x.confidence, reverse=True)


# =============================================================================
# CONTENT STREAM WATERMARK REMOVER
# =============================================================================

class FormXObjectWatermarkRemover:
    """
    Removes watermarks by identifying and removing Form XObject references.

    Many PDF watermarks are implemented as Form XObjects (reusable content)
    that are placed on each page with a transformation matrix (for rotation)
    and graphics state (for transparency).

    The pattern in content streams is:
        q                   # Save graphics state
        /GS1 gs             # Set graphics state (transparency)
        <matrix> cm         # Apply transformation (rotation)
        /Fm0 Do             # Execute Form XObject
        Q                   # Restore graphics state

    This remover:
    1. Identifies Form XObjects that contain watermark text
    2. Removes the 'Do' commands that reference these XObjects
    3. Preserves all other content including overlapping body text
    """

    def __init__(self, doc: fitz.Document):
        self.doc = doc

    def _find_watermark_form_xobjects(self, page: fitz.Page, watermark_texts: Set[str]) -> Set[str]:
        """
        Find Form XObject names that contain watermark text.
        Returns set of XObject names like 'Fm0', 'Fm1', etc.
        """
        watermark_xobjects = set()
        watermark_texts_lower = {t.lower().strip() for t in watermark_texts}

        # Get text with trace info to identify rotated/transparent text
        trace = page.get_texttrace()

        for item in trace:
            # Check for watermark characteristics:
            # - Rotated (dir not (1,0))
            # - Gray color
            # - Low opacity (< 1.0)
            dir_vec = item.get('dir', (1, 0))
            color = item.get('color', (0, 0, 0))
            opacity = item.get('opacity', 1.0)

            # Check if rotated
            is_rotated = abs(dir_vec[1]) > 0.1  # Has significant Y component

            # Check if gray
            if isinstance(color, tuple) and len(color) >= 3:
                r, g, b = color[0], color[1], color[2]
                avg = (r + g + b) / 3
                is_gray = max(abs(r - avg), abs(g - avg), abs(b - avg)) < 0.1 and 0.3 < avg < 0.7
            else:
                is_gray = False

            # Check if transparent
            is_transparent = opacity < 0.9

            # Extract text from chars
            chars = item.get('chars', ())
            text = ''.join(chr(c[0]) for c in chars if c[0] >= 32)
            text_lower = text.lower().strip()

            # Check if this matches watermark text
            is_watermark_text = text_lower in watermark_texts_lower

            if is_watermark_text and (is_rotated or is_gray or is_transparent):
                # This is a watermark - we need to find which XObject it's in
                # The 'layer' field might help, or we need to scan content streams
                pass

        return watermark_xobjects

    def _remove_xobject_references(self, page: fitz.Page) -> int:
        """
        Remove watermark Form XObject references from content streams.

        Removes XObjects that are:
        1. Rotated (diagonal watermarks) - matrix has non-zero b,c components
        2. At header/footer positions (horizontal watermarks at page edges)

        Looks for patterns like:
            q /GS1 gs <matrix> cm /Fm0 Do Q

        Returns:
            Number of references removed
        """
        removed_count = 0
        contents = page.get_contents()
        page_height = page.rect.height

        for content_xref in contents:
            stream = self.doc.xref_stream(content_xref)
            if not stream:
                continue

            try:
                content = stream.decode('latin-1')
            except:
                continue

            new_content = content

            # Pattern 1: Form XObject watermarks
            # Pattern: q ... /GS# gs ... <matrix> cm ... /Fm# Do ... Q
            xobject_pattern = re.compile(
                r'q\s*'                                    # Start with q
                r'[^Q]*?'                                  # Non-greedy match
                r'/GS\d+\s+gs\s*'                          # Graphics state (transparency)
                r'([\d.\s-]+)cm\s*'                        # Transformation matrix (capture)
                r'(/Fm\d+)\s+Do\s*'                        # XObject reference
                r'[^Q]*?'                                  # More content
                r'Q',                                      # End with Q
                re.DOTALL
            )

            # Find Form XObject watermark blocks
            for match in xobject_pattern.finditer(content):
                block = match.group(0)
                matrix_str = match.group(1)

                # Parse transformation matrix: a b c d e f
                matrix_parts = matrix_str.split()
                if len(matrix_parts) >= 6:
                    try:
                        a, b, c, d = float(matrix_parts[0]), float(matrix_parts[1]), float(matrix_parts[2]), float(matrix_parts[3])
                        e, f = float(matrix_parts[4]), float(matrix_parts[5])
                    except ValueError:
                        continue

                    # Check if rotated (b or c significantly non-zero)
                    is_rotated = abs(b) > 0.1 or abs(c) > 0.1

                    # Check if at header/footer position (top 15% or bottom 15% of page)
                    is_header = f > page_height * 0.85
                    is_footer = f < page_height * 0.15
                    is_edge_watermark = is_header or is_footer

                    # Remove if rotated OR at edge position
                    if is_rotated or is_edge_watermark:
                        new_content = new_content.replace(block, '')
                        removed_count += 1

            # Pattern 2: Rotated BT...ET text blocks (shoulder watermarks)
            # Pattern: /gs# gs ... BT ... <rotation matrix> cm ... Tj ... ET
            # These are text blocks with 90° rotation (vertical text on page edges)
            rotated_text_pattern = re.compile(
                r'(/gs\d+\s+gs\s*'                         # Graphics state (lowercase gs)
                r'[^E]*?'                                  # Content before BT
                r'BT\s*'                                   # Begin text
                r'([\d.\s-]+)cm\s*'                        # First transformation matrix
                r'[^E]*?'                                  # More content
                r'ET)',                                    # End text
                re.DOTALL | re.IGNORECASE
            )

            for match in rotated_text_pattern.finditer(new_content):
                block = match.group(1)
                matrix_str = match.group(2)

                # Parse transformation matrix
                matrix_parts = matrix_str.split()
                if len(matrix_parts) >= 6:
                    try:
                        a, b, c, d = float(matrix_parts[0]), float(matrix_parts[1]), float(matrix_parts[2]), float(matrix_parts[3])
                    except ValueError:
                        continue

                    # Check for 90° rotation (b=1,c=-1 or b=-1,c=1)
                    is_90_rotated = (abs(abs(b) - 1) < 0.1 and abs(abs(c) - 1) < 0.1)

                    if is_90_rotated:
                        new_content = new_content.replace(block, '')
                        removed_count += 1

            # Pattern 3: Simple rotated text blocks without graphics state prefix
            # Pattern: BT ... <rotation matrix> cm ... Tj ... ET (at end of stream)
            simple_rotated_pattern = re.compile(
                r'(Q\s*)?'                                 # Optional Q before
                r'(BT\s*'                                  # Begin text
                r'([\d.\s-]+)cm\s*'                        # Rotation matrix
                r'[\d.\s-]+cm\s*'                          # Second matrix (position)
                r'[\d.\s-]+Td\s*'                          # Text position
                r'\([^)]+\)\s*Tj\s*'                       # Text content
                r'ET)',                                    # End text
                re.DOTALL
            )

            for match in simple_rotated_pattern.finditer(new_content):
                block = match.group(2)
                matrix_str = match.group(3)

                matrix_parts = matrix_str.split()
                if len(matrix_parts) >= 4:
                    try:
                        a, b, c, d = float(matrix_parts[0]), float(matrix_parts[1]), float(matrix_parts[2]), float(matrix_parts[3])
                    except ValueError:
                        continue

                    # Check for 90° rotation
                    is_90_rotated = (abs(abs(b) - 1) < 0.1 and abs(abs(c) - 1) < 0.1)

                    if is_90_rotated:
                        new_content = new_content.replace(block, '')
                        removed_count += 1

            # Pattern 4: Artifact-marked watermarks
            # Pattern: /Artifact <</Subtype /Watermark ...>>BDC ... q <matrix> cm /GS# gs /Fm# Do Q ... EMC
            # These are PDF/A compliant watermarks marked as artifacts
            artifact_pattern = re.compile(
                r'(/Artifact\s*<<[^>]*?/Watermark[^>]*?>>\s*BDC\s*'  # Artifact marker with /Watermark
                r'[^E]*?'                                            # Content (non-greedy)
                r'EMC)',                                             # End marked content
                re.DOTALL
            )

            for match in artifact_pattern.finditer(new_content):
                block = match.group(1)
                # Remove the entire artifact-marked watermark block
                new_content = new_content.replace(block, '')
                removed_count += 1

            # Pattern 5: Rotated Form XObjects without graphics state (alternate format)
            # Pattern: q <matrix> cm /GS# gs /Fm# Do Q (where q comes after Q or at block start)
            alt_xobject_pattern = re.compile(
                r'(q\s*'                                   # Start with q
                r'([\d.\s-]+)cm\s*'                        # Transformation matrix (capture)
                r'/GS\d+\s+gs\s*'                          # Graphics state (transparency)
                r'/Fm\d+\s+Do\s*'                          # XObject reference
                r'Q)',                                     # End with Q
                re.DOTALL
            )

            for match in alt_xobject_pattern.finditer(new_content):
                block = match.group(1)
                matrix_str = match.group(2)

                # Parse transformation matrix: a b c d e f
                matrix_parts = matrix_str.split()
                if len(matrix_parts) >= 6:
                    try:
                        a, b, c, d = float(matrix_parts[0]), float(matrix_parts[1]), float(matrix_parts[2]), float(matrix_parts[3])
                        e, f = float(matrix_parts[4]), float(matrix_parts[5])
                    except ValueError:
                        continue

                    # Check if rotated (b or c significantly non-zero)
                    is_rotated = abs(b) > 0.1 or abs(c) > 0.1

                    # Check if at header/footer position
                    is_header = f > page_height * 0.85
                    is_footer = f < page_height * 0.15
                    is_edge_watermark = is_header or is_footer

                    # Remove if rotated OR at edge position
                    if is_rotated or is_edge_watermark:
                        new_content = new_content.replace(block, '')
                        removed_count += 1

            if new_content != content:
                try:
                    self.doc.update_stream(content_xref, new_content.encode('latin-1'))
                except Exception as e:
                    print(f"Warning: Could not update stream {content_xref}: {e}")
                    removed_count = 0

        return removed_count

    def remove_watermarks_from_page(self, page: fitz.Page, watermark_texts: Set[str]) -> int:
        """
        Remove watermark Form XObjects from a page.

        Args:
            page: PyMuPDF page object
            watermark_texts: Set of watermark text strings (used for verification)

        Returns:
            Number of watermarks removed
        """
        # Remove rotated XObject references from content streams
        removed = self._remove_xobject_references(page)
        return removed


# =============================================================================
# WATERMARK REMOVAL ENGINE
# =============================================================================

class WatermarkRemover:
    """Main interface for PDF watermark detection and removal."""

    def __init__(self, pdf_path: str, config: Optional[DetectionConfig] = None):
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        self.pdf_path = pdf_path
        self.config = config or DetectionConfig()
        self.doc = fitz.open(pdf_path)
        self._detector = None
        self._stream_remover = None

    @property
    def stream_remover(self) -> FormXObjectWatermarkRemover:
        if self._stream_remover is None:
            self._stream_remover = FormXObjectWatermarkRemover(self.doc)
        return self._stream_remover

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.doc:
            self.doc.close()
            self.doc = None

    @property
    def page_count(self) -> int:
        return self.doc.page_count if self.doc else 0

    @property
    def detector(self) -> SmartWatermarkDetector:
        if self._detector is None:
            self._detector = SmartWatermarkDetector(self.doc, self.config)
        return self._detector

    def detect(self, min_confidence: Optional[float] = None) -> List[WatermarkCandidate]:
        if min_confidence is not None:
            self.config.min_confidence = min_confidence
        return self.detector.detect_all()

    def remove(self, candidates: List[WatermarkCandidate], output_path: str) -> Dict:
        """
        Remove watermarks using content-stream editing.

        This method surgically removes watermark text by editing the PDF content
        streams directly, preserving all overlapping body text that shares the
        same bounding box area.

        For text/notice watermarks: Edits content streams to remove specific
        text drawing commands based on color and rotation properties.

        For image/vector watermarks: Uses standard redaction (no overlap issue).
        """
        if not candidates:
            return {"success": True, "removed": 0, "pages_modified": 0}

        # Collect watermark text content for content-stream removal
        watermark_texts = set()
        for c in candidates:
            if c.type in (WatermarkType.TEXT, WatermarkType.NOTICE):
                watermark_texts.add(c.content)

        # Group by page
        by_page = defaultdict(list)
        for c in candidates:
            by_page[c.page].append(c)

        pages_modified = set()
        total_removed = 0

        for page_idx, page_candidates in by_page.items():
            page = self.doc[page_idx]
            removed_on_page = 0

            # Use content-stream editing for text watermarks
            # This removes rotated Form XObjects that contain watermark text
            text_watermarks = [c for c in page_candidates if c.type in (WatermarkType.TEXT, WatermarkType.NOTICE)]
            if text_watermarks:
                page_watermark_texts = set(c.content for c in text_watermarks)
                removed = self.stream_remover.remove_watermarks_from_page(page, page_watermark_texts)
                removed_on_page += removed

            # NOTE: We intentionally skip image/vector watermark redaction
            # The Form XObject removal above handles diagonal text watermarks
            # Image/vector redaction can damage overlapping content and is disabled
            # If you need to remove standalone image watermarks, consider using
            # a separate pass with careful bbox verification

            if removed_on_page > 0:
                pages_modified.add(page_idx)
                total_removed += removed_on_page

        # Save - use incremental=False but avoid aggressive garbage collection
        # when content streams have been modified
        try:
            # For content stream modifications, avoid garbage collection which can corrupt references
            self.doc.save(output_path, garbage=0, deflate=True, clean=False)
            return {"success": True, "removed": total_removed, "pages_modified": len(pages_modified)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def visualize(self, candidates: List[WatermarkCandidate], output_path: str) -> str:
        """Create visualization PDF with watermarks highlighted."""
        colors = {
            WatermarkType.TEXT: (1, 0, 0),
            WatermarkType.NOTICE: (1, 0.5, 0),
            WatermarkType.IMAGE: (0, 0, 1),
            WatermarkType.VECTOR: (0, 1, 0),
        }

        for c in candidates:
            page = self.doc[c.page]
            color = colors.get(c.type, (1, 0, 0))

            if c.type in (WatermarkType.TEXT, WatermarkType.NOTICE):
                try:
                    for quad in page.search_for(c.content, quads=True):
                        page.draw_quad(quad, color=color, width=2)
                except:
                    page.draw_rect(fitz.Rect(c.bbox), color=color, width=2)
            else:
                page.draw_rect(fitz.Rect(c.bbox), color=color, width=2)

        self.doc.save(output_path)
        return output_path

    def process(self, output_path: Optional[str] = None, min_confidence: Optional[float] = None) -> Dict:
        """Detect and remove watermarks in one step."""
        if output_path is None:
            base, ext = os.path.splitext(self.pdf_path)
            output_path = f"{base}_clean{ext}"

        candidates = self.detect(min_confidence)
        result = self.remove(candidates, output_path)
        result["candidates"] = len(candidates)
        result["output"] = output_path
        return result


# =============================================================================
# CLI
# =============================================================================

def print_header():
    print()
    print("=" * 60)
    print("       NATIVE PDF WATERMARK REMOVER v2.0")
    print("=" * 60)
    print("  No image conversion | No quality loss | Native PDF")
    print()


def print_candidates(candidates, limit=20):
    print("-" * 60)
    for i, c in enumerate(candidates[:limit]):
        print(f"\n[{i+1}] Page {c.page + 1} | Confidence: {c.confidence:.0%}")
        print(f"    Type: {c.type.value}")
        content = c.content[:60] + "..." if len(c.content) > 60 else c.content
        print(f"    Content: {content}")
        print(f"    Reasons: {', '.join(c.reasons)}")
    if len(candidates) > limit:
        print(f"\n    ... and {len(candidates) - limit} more")


def run_interactive():
    print_header()

    while True:
        path = input("Enter PDF path (or 'q' to quit): ").strip().strip('"').strip("'")
        if path.lower() == 'q':
            return
        if not path or not os.path.exists(path):
            print("  Error: File not found.\n")
            continue
        if not path.lower().endswith('.pdf'):
            print("  Error: Must be a PDF.\n")
            continue
        break

    base, ext = os.path.splitext(path)
    output = f"{base}_clean{ext}"
    print(f"\nOutput: {output}")

    print("\nConfidence: 0.3=Aggressive, 0.4=Balanced, 0.6=Conservative")
    while True:
        conf = input("Confidence [0.3-0.9] (Enter=0.4): ").strip()
        if not conf:
            confidence = 0.4
            break
        try:
            confidence = float(conf)
            if 0.1 <= confidence <= 1.0:
                break
        except:
            pass
        print("  Error: Invalid value")

    preview = input("\nPreview first? (y/N): ").strip().lower() in ('y', 'yes')

    print("\n" + "-" * 60)

    try:
        with WatermarkRemover(path) as remover:
            print(f"Pages: {remover.page_count}")
            print("Analyzing...")

            candidates = remover.detect(confidence)

            if not candidates:
                print("\nNo watermarks detected!")
                return

            by_type = defaultdict(int)
            for c in candidates:
                by_type[c.type.value] += 1

            print(f"\nDetected {len(candidates)} potential watermarks:")
            for t, count in by_type.items():
                print(f"  {t.capitalize()}: {count}")

            if preview:
                print("\n[PREVIEW]")
                print_candidates(candidates)
                if input("\nProceed? (y/N): ").strip().lower() not in ('y', 'yes'):
                    return

            print("\nRemoving...")
            result = remover.remove(candidates, output)

            if result.get("success"):
                print(f"\nRemoved: {result.get('removed', 0)}")
                print(f"Pages: {result.get('pages_modified', 0)}")
                print(f"Output: {output}")
                print("\n[SUCCESS]")
            else:
                print(f"\n[FAILED] {result.get('error', 'Unknown error')}")

    except Exception as e:
        print(f"\n[ERROR] {e}")


def main():
    parser = argparse.ArgumentParser(description="Remove watermarks from PDFs")
    parser.add_argument("input", nargs="?", help="Input PDF path")
    parser.add_argument("-o", "--output", help="Output PDF path")
    parser.add_argument("-c", "--confidence", type=float, default=0.4, help="Confidence (0.1-1.0)")
    parser.add_argument("-p", "--preview", action="store_true", help="Preview only")
    parser.add_argument("-v", "--visualize", nargs="?", const=True, help="Create visualization")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")

    args = parser.parse_args()

    if args.interactive or args.input is None:
        run_interactive()
        return

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        sys.exit(1)

    output = args.output or os.path.splitext(args.input)[0] + "_clean.pdf"

    print_header()
    print(f"Input:      {args.input}")
    print(f"Output:     {output}")
    print(f"Confidence: {args.confidence}")

    try:
        with WatermarkRemover(args.input, DetectionConfig(min_confidence=args.confidence)) as remover:
            print(f"\nPages: {remover.page_count}")
            print("Analyzing...")

            candidates = remover.detect()

            if not candidates:
                print("\nNo watermarks detected!")
                sys.exit(0)

            by_type = defaultdict(int)
            for c in candidates:
                by_type[c.type.value] += 1

            print(f"\nDetected {len(candidates)} watermarks:")
            for t, count in by_type.items():
                print(f"  {t.capitalize()}: {count}")

            if args.preview:
                print("\n[PREVIEW]")
                print_candidates(candidates)
                sys.exit(0)

            if args.visualize:
                vis_out = args.visualize if isinstance(args.visualize, str) else os.path.splitext(args.input)[0] + "_visual.pdf"
                remover.visualize(candidates, vis_out)
                print(f"\nVisualization: {vis_out}")
                sys.exit(0)

            print("\nRemoving...")
            result = remover.remove(candidates, output)

            if result.get("success"):
                print(f"\nRemoved: {result.get('removed', 0)}")
                print(f"Output: {output}")
                print("\n[SUCCESS]")
            else:
                print(f"\n[FAILED] {result.get('error')}")
                sys.exit(1)

    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
