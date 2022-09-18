#! /usr/bin/env python
from __future__ import annotations

import shutil
import subprocess
import typing
from pathlib import Path
from typing import TYPE_CHECKING

import timeout_decorator
import zope.interface
from dacite import from_dict
from PyPDF2 import PdfFileReader

import colrev.env.package_manager
import colrev.env.utils
import colrev.record

if TYPE_CHECKING:
    import colrev.ops.pdf_prep

# pylint: disable=too-few-public-methods


@zope.interface.implementer(colrev.env.package_manager.PDFPrepPackageInterface)
class PDFLastPage:
    """Prepare PDFs by removing unnecessary last pages (e.g. copyright notices, cited-by infos)"""

    settings_class = colrev.env.package_manager.DefaultSettings

    def __init__(
        self,
        *,
        pdf_prep_operation: colrev.ops.pdf_prep.PDFPrep,  # pylint: disable=unused-argument
        settings: dict,
    ) -> None:
        self.settings = from_dict(data_class=self.settings_class, data=settings)

    @timeout_decorator.timeout(60, use_signals=False)
    def prep_pdf(
        self,
        pdf_prep_operation: colrev.ops.pdf_prep.PDFPrep,
        record: colrev.record.Record,
        pad: int,  # pylint: disable=unused-argument
    ) -> dict:

        local_index = pdf_prep_operation.review_manager.get_local_index()
        lp_path = local_index.local_environment_path / Path(".lastpages")
        lp_path.mkdir(exist_ok=True)

        def __get_last_pages(*, pdf: str) -> typing.List[int]:
            # for corrupted PDFs pdftotext seems to be more robust than
            # pdf_reader.getPage(0).extractText()

            last_pages: typing.List[int] = []
            pdf_reader = PdfFileReader(pdf, strict=False)
            last_page_nr = pdf_reader.getNumPages()

            pdf_hash_service = pdf_prep_operation.review_manager.get_pdf_hash_service()

            last_page_average_hash_16 = pdf_hash_service.get_pdf_hash(
                pdf_path=Path(pdf),
                page_nr=last_page_nr,
                hash_size=16,
            )

            if last_page_nr == 1:
                return last_pages

            # Note : to generate hashes from a directory containing single-page PDFs:
            # colrev pdf-prep --get_hashes path
            last_page_hashes = [
                "ffffffffffffffffffffffffffffffffffffffffffffffffffffffff83ff83ff",
                "ffff80038007ffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "c3fbc003c003ffc3ff83ffc3ffffffffffffffffffffffffffffffffffffffff",
                "ffff80038007ffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "ffff80038001ffff7fff7fff7fff7fff7fff7fff7fff7fffffffffffffffffff",
                "ffff80008003ffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "ffff80038007ffffffffffffffffffffffffffffffffffffffffffffffffffff",
            ]

            if str(last_page_average_hash_16) in last_page_hashes:
                last_pages.append(last_page_nr - 1)

            res = subprocess.run(
                [
                    "/usr/bin/pdftotext",
                    pdf,
                    "-f",
                    str(last_page_nr),
                    "-l",
                    str(last_page_nr),
                    "-enc",
                    "UTF-8",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            last_page_text = (
                res.stdout.decode("utf-8").replace(" ", "").replace("\n", "").lower()
            )

            # ME Sharpe last page
            if (
                "propertyofm.e.sharpeinc.anditscontentmayno"
                + "tbecopiedoremailedtomultiplesi"
                + "tesorpostedtoalistservwithoutthecopyrightholder"
                in last_page_text
            ):
                last_pages.append(last_page_nr - 1)

            return list(set(last_pages))

        last_pages = __get_last_pages(pdf=record.data["file"])
        if not last_pages:
            return record.data
        if last_pages:
            original = pdf_prep_operation.review_manager.path / Path(
                record.data["file"]
            )
            file_copy = pdf_prep_operation.review_manager.path / Path(
                record.data["file"].replace(".pdf", "_wo_lp.pdf")
            )
            shutil.copy(original, file_copy)

            record.extract_pages(
                pages=last_pages,
                project_path=pdf_prep_operation.review_manager.path,
                save_to_path=lp_path,
            )
            pdf_prep_operation.review_manager.report_logger.info(
                f'removed last page for ({record.data["ID"]})'
            )
        return record.data


if __name__ == "__main__":
    pass