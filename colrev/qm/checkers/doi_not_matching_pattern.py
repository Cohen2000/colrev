#! /usr/bin/env python
"""Checker for doi-not-matching-pattern."""
from __future__ import annotations

import re

import colrev.qm.quality_model
from colrev.constants import DefectCodes
from colrev.constants import Fields

# pylint: disable=too-few-public-methods


class DOIPatternChecker:
    """The DOIPatternChecker"""

    msg = DefectCodes.DOI_NOT_MATCHING_PATTERN
    # https://www.crossref.org/blog/dois-and-matching-regular-expressions/
    __DOI_REGEX = r"^10.\d{4,9}\/"

    def __init__(self, quality_model: colrev.qm.quality_model.QualityModel) -> None:
        self.quality_model = quality_model

    def run(self, *, record: colrev.record.Record) -> None:
        """Run the doi-not-matching-pattern checks"""

        if Fields.DOI not in record.data:
            return

        if not re.match(self.__DOI_REGEX, record.data[Fields.DOI]):
            record.add_masterdata_provenance_note(key=Fields.DOI, note=self.msg)
        else:
            record.remove_masterdata_provenance_note(key=Fields.DOI, note=self.msg)


def register(quality_model: colrev.qm.quality_model.QualityModel) -> None:
    """Register the checker"""
    quality_model.register_checker(DOIPatternChecker(quality_model))
