#! /usr/bin/env python
import configparser
import csv
import datetime
import itertools
import json
import os
import pkgutil
import re
import tempfile
import typing
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
import zope.interface
from dacite import from_dict

import colrev.exceptions as colrev_exceptions
import colrev.process
import colrev.record


@zope.interface.implementer(colrev.process.DataEndpoint)
class ManuscriptEndpoint:

    NEW_RECORD_SOURCE_TAG = "<!-- NEW_RECORD_SOURCE -->"
    """Tag for appending new records in paper.md

    In the paper.md, the IDs of new records marked for synthesis
    will be appended after this tag.

    If IDs are moved to other parts of the manuscript,
    the corresponding record will be marked as rev_synthesized."""

    def __init__(self, *, data, settings):
        self.settings = from_dict(
            data_class=colrev.process.DefaultSettings, data=settings
        )

    def get_default_setup(self):

        manuscript_endpoint_details = {
            "endpoint": "MANUSCRIPT",
            "paper_endpoint_version": "0.1",
            "word_template": ManuscriptEndpoint.retrieve_default_word_template(),
            "csl_style": ManuscriptEndpoint.retrieve_default_csl(),
        }

        return manuscript_endpoint_details

    @classmethod
    def retrieve_default_word_template(cls) -> str:
        template_name = "APA-7.docx"

        filedata = pkgutil.get_data(__name__, str(Path("template/APA-7.docx")))
        if filedata:
            with open(Path(template_name), "wb") as file:
                file.write(filedata)

        return template_name

    @classmethod
    def retrieve_default_csl(cls) -> str:
        csl_link = (
            "https://raw.githubusercontent.com/"
            + "citation-style-language/styles/master/apa.csl"
        )
        ret = requests.get(csl_link, allow_redirects=True)
        with open(Path(csl_link).name, "wb") as file:
            file.write(ret.content)
        csl = Path(csl_link).name
        return csl

    def check_new_record_source_tag(self, review_manager) -> None:
        paper = review_manager.paths["PAPER"]
        with open(paper, encoding="utf-8") as file:
            for line in file:
                if self.NEW_RECORD_SOURCE_TAG in line:
                    return
        raise ManuscriptRecordSourceTagError(
            f"Did not find {self.NEW_RECORD_SOURCE_TAG} tag in {paper}"
        )

    def update_manuscript(
        self,
        review_manager,
        records: typing.Dict,
        synthesized_record_status_matrix: dict,
    ) -> typing.Dict:
        def authorship_heuristic() -> str:
            git_repo = review_manager.dataset.get_repo()
            commits_list = list(git_repo.iter_commits())
            commits_authors = []
            for commit in commits_list:
                committer = git_repo.git.show("-s", "--format=%cn", commit.hexsha)
                if "GitHub" == committer:
                    continue
                commits_authors.append(committer)
                # author = git_repo.git.show("-s", "--format=%an", commit.hexsha)
                # mail = git_repo.git.show("-s", "--format=%ae", commit.hexsha)
            author = ", ".join(dict(Counter(commits_authors)))
            return author

        def get_data_page_missing(paper: Path, record_id_list: list) -> list:
            available = []
            with open(paper, encoding="utf-8") as file:
                line = file.read()
                for record in record_id_list:
                    if record in line:
                        available.append(record)

            return list(set(record_id_list) - set(available))

        paper = review_manager.paths["PAPER"]
        paper_relative = review_manager.paths["PAPER_RELATIVE"]

        def add_missing_records_to_manuscript(
            *, review_manager, paper: Path, missing_records: list
        ):
            # pylint: disable=consider-using-with
            temp = tempfile.NamedTemporaryFile()
            paper.rename(temp.name)
            with open(temp.name, encoding="utf-8") as reader, open(
                paper, "w", encoding="utf-8"
            ) as writer:
                appended, completed = False, False
                line = reader.readline()
                while line != "":
                    if self.NEW_RECORD_SOURCE_TAG in line:
                        if "_Records to synthesize" not in line:
                            line = "_Records to synthesize_:" + line + "\n"
                            writer.write(line)
                        else:
                            writer.write(line)
                            writer.write("\n")

                        for missing_record in missing_records:
                            writer.write(missing_record)
                            review_manager.report_logger.info(
                                # f" {missing_record}".ljust(self.__PAD, " ")
                                f" {missing_record}"
                                + f" added to {paper.name}"
                            )

                            review_manager.logger.info(
                                # f" {missing_record}".ljust(self.__PAD, " ")
                                f" {missing_record}"
                                + f" added to {paper.name}"
                            )

                        # skip empty lines between to connect lists
                        line = reader.readline()
                        if "\n" != line:
                            writer.write(line)

                        appended = True

                    elif appended and not completed:
                        if "- @" == line[:3]:
                            writer.write(line)
                        else:
                            if "\n" != line:
                                writer.write("\n")
                            writer.write(line)
                            completed = True
                    else:
                        writer.write(line)
                    line = reader.readline()

                if not appended:
                    msg = (
                        f"Marker {self.NEW_RECORD_SOURCE_TAG} not found in "
                        + f"{paper.name}. Adding records at the end of "
                        + "the document."
                    )
                    review_manager.report_logger.warning(msg)
                    review_manager.logger.warning(msg)

                    if line != "\n":
                        writer.write("\n")
                    marker = f"{self.NEW_RECORD_SOURCE_TAG}_Records to synthesize_:\n\n"
                    writer.write(marker)
                    for missing_record in missing_records:
                        writer.write(missing_record)
                        review_manager.report_logger.info(
                            # f" {missing_record}".ljust(self.__PAD, " ") + " added"
                            f" {missing_record} added"
                        )
                        review_manager.logger.info(
                            # f" {missing_record}".ljust(self.__PAD, " ") + " added"
                            f" {missing_record} added"
                        )

        if not paper.is_file():
            # missing_records = synthesized_record_status_matrix.keys()

            review_manager.report_logger.info("Creating manuscript")
            review_manager.logger.info("Creating manuscript")

            title = "Manuscript template"
            readme_file = review_manager.paths["README"]
            if readme_file.is_file():
                with open(readme_file, encoding="utf-8") as file:
                    title = file.readline()
                    title = title.replace("# ", "").replace("\n", "")

            author = authorship_heuristic()

            review_type = review_manager.settings.project.review_type

            r_type_path = str(review_type).replace(" ", "_").replace("-", "_")
            paper_resource_path = (
                Path(f"template/review_type/{r_type_path}/") / paper_relative
            )
            try:
                review_manager.retrieve_package_file(
                    template_file=paper_resource_path, target=paper
                )
            except Exception:
                paper_resource_path = Path("template/") / paper_relative
                review_manager.retrieve_package_file(
                    template_file=paper_resource_path, target=paper
                )

            review_manager.dataset.inplace_change(
                filename=paper,
                old_string="{{review_type}}",
                new_string=str(review_type),
            )
            review_manager.dataset.inplace_change(
                filename=paper, old_string="{{project_title}}", new_string=title
            )
            review_manager.dataset.inplace_change(
                filename=paper, old_string="{{author}}", new_string=author
            )
            review_manager.logger.info(
                f"Please update title and authors in {paper.name}"
            )

        review_manager.report_logger.info("Updating manuscript")
        review_manager.logger.info("Updating manuscript")
        missing_records = get_data_page_missing(
            paper, list(synthesized_record_status_matrix.keys())
        )
        missing_records = sorted(missing_records)
        review_manager.logger.debug(f"missing_records: {missing_records}")

        if 0 == len(missing_records):
            review_manager.report_logger.info(f"All records included in {paper.name}")
            review_manager.logger.info(f"All records included in {paper.name}")
        else:
            add_missing_records_to_manuscript(
                review_manager=review_manager,
                paper=paper,
                missing_records=[
                    "\n- @" + missing_record + "\n"
                    for missing_record in missing_records
                ],
            )
            nr_records_added = len(missing_records)
            review_manager.report_logger.info(
                f"{nr_records_added} records added to {paper.name}"
            )
            review_manager.logger.info(
                f"{nr_records_added} records added to {paper.name}"
            )

        review_manager.dataset.add_changes(path=review_manager.paths["PAPER_RELATIVE"])

        return records

    def update_data(self, data, records: dict, synthesized_record_status_matrix: dict):
        # Update manuscript
        records = self.update_manuscript(
            data.review_manager, records, synthesized_record_status_matrix
        )

    def update_record_status_matrix(
        self, data, synthesized_record_status_matrix, endpoint_identifier
    ):
        def get_to_synthesize_in_manuscript(
            paper: Path, records_for_synthesis: list
        ) -> list:
            in_manuscript_to_synthesize = []
            if paper.is_file():
                with open(paper, encoding="utf-8") as file:
                    for line in file:
                        if self.NEW_RECORD_SOURCE_TAG in line:
                            while line != "":
                                line = file.readline()
                                if re.search(r"- @.*", line):
                                    record_id = re.findall(r"- @(.*)$", line)
                                    in_manuscript_to_synthesize.append(record_id[0])
                                    if line == "\n":
                                        break

                in_manuscript_to_synthesize = [
                    x for x in in_manuscript_to_synthesize if x in records_for_synthesis
                ]
            else:
                in_manuscript_to_synthesize = records_for_synthesis
            return in_manuscript_to_synthesize

        def get_synthesized_ids_paper(
            paper: Path, synthesized_record_status_matrix
        ) -> list:

            in_manuscript_to_synthesize = get_to_synthesize_in_manuscript(
                paper, list(synthesized_record_status_matrix.keys())
            )
            # Assuming that all records have been added to the paper before
            synthesized_ids = [
                x
                for x in list(synthesized_record_status_matrix.keys())
                if x not in in_manuscript_to_synthesize
            ]

            return synthesized_ids

        # Update status / synthesized_record_status_matrix
        synthesized_in_manuscript = get_synthesized_ids_paper(
            data.review_manager.paths["PAPER"],
            synthesized_record_status_matrix,
        )
        for syn_id in synthesized_in_manuscript:
            if syn_id in synthesized_record_status_matrix:
                synthesized_record_status_matrix[syn_id][endpoint_identifier] = True
            else:
                print(f"Error: {syn_id} not int {synthesized_in_manuscript}")


@zope.interface.implementer(colrev.process.DataEndpoint)
class StructuredDataEndpoint:
    def __init__(self, *, data, settings):
        self.settings = from_dict(
            data_class=colrev.process.DefaultSettings, data=settings
        )

    def get_default_setup(self):
        structured_endpoint_details = {
            "endpoint": "STRUCTURED",
            "structured_data_endpoint_version": "0.1",
            "fields": [
                {
                    "name": "field name",
                    "explanation": "explanation",
                    "data_type": "data type",
                }
            ],
        }
        return structured_endpoint_details

    def update_data(self, data, records: dict, synthesized_record_status_matrix: dict):
        def update_structured_data(
            review_manager,
            synthesized_record_status_matrix: dict,
        ) -> typing.Dict:

            data = review_manager.paths["DATA"]

            if not data.is_file():

                coding_dimensions_str = input(
                    "\n\nEnter columns for data extraction (comma-separted)"
                )
                coding_dimensions = coding_dimensions_str.replace(" ", "_").split(",")

                data = []
                for included_id in list(synthesized_record_status_matrix.keys()):
                    item = [[included_id], ["TODO"] * len(coding_dimensions)]
                    data.append(list(itertools.chain(*item)))

                data_df = pd.DataFrame(data, columns=["ID"] + coding_dimensions)
                data_df.sort_values(by=["ID"], inplace=True)

                data_df.to_csv(data, index=False, quoting=csv.QUOTE_ALL)

            else:

                nr_records_added = 0

                data_df = pd.read_csv(data, dtype=str)

                for record_id in list(synthesized_record_status_matrix.keys()):
                    # skip when already available
                    if 0 < len(data_df[data_df["ID"].str.startswith(record_id)]):
                        continue

                    add_record = pd.DataFrame({"ID": [record_id]})
                    add_record = add_record.reindex(
                        columns=data_df.columns, fill_value="TODO"
                    )
                    data_df = pd.concat(
                        [data_df, add_record], axis=0, ignore_index=True
                    )
                    nr_records_added = nr_records_added + 1

                data_df.sort_values(by=["ID"], inplace=True)

                data_df.to_csv(data, index=False, quoting=csv.QUOTE_ALL)

                review_manager.report_logger.info(
                    f"{nr_records_added} records added ({data})"
                )
                review_manager.logger.info(f"{nr_records_added} records added ({data})")

            return records

        records = update_structured_data(
            data.review_manager, synthesized_record_status_matrix
        )

        data.review_manager.dataset.add_changes(
            path=data.review_manager.paths["DATA_RELATIVE"]
        )

    def update_record_status_matrix(
        self, data, synthesized_record_status_matrix, endpoint_identifier
    ):
        def get_data_extracted(data: Path, records_for_data_extraction: list) -> list:
            data_extracted = []
            data_df = pd.read_csv(data)

            for record in records_for_data_extraction:
                drec = data_df.loc[data_df["ID"] == record]
                if 1 == drec.shape[0]:
                    if "TODO" not in drec.iloc[0].tolist():
                        data_extracted.append(drec.loc[drec.index[0], "ID"])

            data_extracted = [
                x for x in data_extracted if x in records_for_data_extraction
            ]
            return data_extracted

        def get_structured_data_extracted(
            synthesized_record_status_matrix: typing.Dict, data: Path
        ) -> list:

            if not data.is_file():
                return []

            data_extracted = get_data_extracted(
                data, list(synthesized_record_status_matrix.keys())
            )
            data_extracted = [
                x
                for x in data_extracted
                if x in list(synthesized_record_status_matrix.keys())
            ]
            return data_extracted

        data_path = data.review_manager.paths["DATA"]
        structured_data_extracted = get_structured_data_extracted(
            synthesized_record_status_matrix, data_path
        )

        for syn_id in structured_data_extracted:
            if syn_id in synthesized_record_status_matrix:
                synthesized_record_status_matrix[syn_id][endpoint_identifier] = True
            else:
                print(f"Error: {syn_id} not int " f"{synthesized_record_status_matrix}")


@zope.interface.implementer(colrev.process.DataEndpoint)
class EndnoteEndpoint:
    def __init__(self, *, data, settings):
        self.settings = from_dict(
            data_class=colrev.process.DefaultSettings, data=settings
        )

    def get_default_setup(self):
        endnote_endpoint_details = {
            "endpoint": "ENDNOTE",
            "endnote_data_endpoint_version": "0.1",
            "config": {
                "path": "data/endnote",
            },
        }
        return endnote_endpoint_details

    def update_data(self, data, records: dict, synthesized_record_status_matrix: dict):
        def zotero_conversion(data):

            zotero_translation_service = (
                data.review_manager.get_zotero_translation_service()
            )
            zotero_translation_service.start_zotero_translators()

            headers = {"Content-type": "text/plain"}
            ret = requests.post(
                "http://127.0.0.1:1969/import",
                headers=headers,
                files={"file": str.encode(data)},
            )
            headers = {"Content-type": "application/json"}
            if "No suitable translators found" == ret.content.decode("utf-8"):
                raise colrev_exceptions.ImportException(
                    "Zotero translators: No suitable translators found"
                )

            try:
                zotero_format = json.loads(ret.content)
                export = requests.post(
                    "http://127.0.0.1:1969/export?format=refer",
                    headers=headers,
                    json=zotero_format,
                )

            except Exception as exc:
                raise colrev_exceptions.ImportException(
                    f"Zotero translators failed ({exc})"
                )

            return export.content

        endpoint_path = Path("data/endnote")
        endpoint_path.mkdir(exist_ok=True, parents=True)

        if not any(Path(endpoint_path).iterdir()):
            data.review_manager.logger.info("Export all")
            export_filepath = endpoint_path / Path("export_part1.enl")

            selected_records = {
                ID: r
                for ID, r in records.items()
                if r["colrev_status"]
                in [
                    colrev.record.RecordState.rev_included,
                    colrev.record.RecordState.rev_synthesized,
                ]
            }

            data = data.review_manager.dataset.parse_bibtex_str(
                recs_dict_in=selected_records
            )

            enl_data = zotero_conversion(data)

            with open(export_filepath, "w", encoding="utf-8") as export_file:
                export_file.write(enl_data.decode("utf-8"))
            data.review_manager.dataset.add_changes(path=str(export_filepath))

        else:

            enl_files = endpoint_path.glob("*.enl")
            file_numbers = []
            exported_ids = []
            for enl_file_path in enl_files:
                file_numbers.append(int(re.findall(r"\d+", str(enl_file_path.name))[0]))
                with open(enl_file_path, encoding="utf-8") as enl_file:
                    for line in enl_file:
                        if "%F" == line[:2]:
                            record_id = line[3:].lstrip().rstrip()
                            exported_ids.append(record_id)

            data.review_manager.logger.info(
                "IDs that have already been exported (in the other export files):"
                f" {exported_ids}"
            )

            selected_records = {
                ID: r
                for ID, r in records.items()
                if r["colrev_status"]
                in [
                    colrev.record.RecordState.rev_included,
                    colrev.record.RecordState.rev_synthesized,
                ]
            }

            if len(selected_records) > 0:

                data = data.review_manager.dataset.parse_bibtex_str(
                    recs_dict_in=selected_records
                )

                enl_data = zotero_conversion(data)

                next_file_number = str(max(file_numbers) + 1)
                export_filepath = endpoint_path / Path(
                    f"export_part{next_file_number}.enl"
                )
                print(export_filepath)
                with open(export_filepath, "w", encoding="utf-8") as file:
                    file.write(enl_data.decode("utf-8"))
                data.review_manager.dataset.add_changes(path=str(export_filepath))

            else:
                data.review_manager.logger.info("No additional records to export")

    def update_record_status_matrix(
        self, data, synthesized_record_status_matrix, endpoint_identifier
    ):
        # Note : automatically set all to True / synthesized
        for syn_id in list(synthesized_record_status_matrix.keys()):
            synthesized_record_status_matrix[syn_id][endpoint_identifier] = True


@zope.interface.implementer(colrev.process.DataEndpoint)
class PRISMAEndpoint:
    def __init__(self, *, data, settings):
        self.settings = from_dict(
            data_class=colrev.process.DefaultSettings, data=settings
        )

    def get_default_setup(self):
        prisma_endpoint_details = {
            "endpoint": "PRISMA",
            "prisma_data_endpoint_version": "0.1",
        }
        return prisma_endpoint_details

    def update_data(self, data, records: dict, synthesized_record_status_matrix: dict):

        prisma_resource_path = Path("template/") / Path("PRISMA.csv")
        prisma_path = Path("data/PRISMA.csv")
        prisma_path.parent.mkdir(exist_ok=True, parents=True)

        if prisma_path.is_file():
            os.remove(prisma_path)
        data.review_manager.retrieve_package_file(
            template_file=prisma_resource_path, target=prisma_path
        )

        stat = data.review_manager.get_status_freq()
        # print(stat)

        prisma_data = pd.read_csv(prisma_path)
        prisma_data["ind"] = prisma_data["data"]
        prisma_data.set_index("ind", inplace=True)
        prisma_data.loc["database_results", "n"] = stat["colrev_status"]["overall"][
            "md_retrieved"
        ]
        prisma_data.loc["duplicates", "n"] = stat["colrev_status"]["currently"][
            "md_duplicates_removed"
        ]
        prisma_data.loc["records_screened", "n"] = stat["colrev_status"]["overall"][
            "rev_prescreen"
        ]
        prisma_data.loc["records_excluded", "n"] = stat["colrev_status"]["overall"][
            "rev_excluded"
        ]
        prisma_data.loc["dbr_assessed", "n"] = stat["colrev_status"]["overall"][
            "rev_screen"
        ]
        prisma_data.loc["new_studies", "n"] = stat["colrev_status"]["overall"][
            "rev_included"
        ]
        # TODO : TBD: if settings.pdf_get.pdf_required_for_screen_and_synthesis = False
        # should the following be included?
        prisma_data.loc["dbr_notretrieved_reports", "n"] = stat["colrev_status"][
            "overall"
        ]["pdf_not_available"]
        prisma_data.loc["dbr_sought_reports", "n"] = stat["colrev_status"]["overall"][
            "rev_prescreen_included"
        ]

        exclusion_stats = []
        for criterion, value in stat["colrev_status"]["currently"]["exclusion"].items():
            exclusion_stats.append(f"Reason {criterion}, {value}")
        prisma_data.loc["dbr_excluded", "n"] = "; ".join(exclusion_stats)

        prisma_data.to_csv(prisma_path, index=False)
        print(f"Exported {prisma_path}")
        print(
            "Diagrams can be created online "
            "at https://estech.shinyapps.io/prisma_flowdiagram/"
        )

        if not stat["completeness_condition"]:
            print("Warning: review not (yet) complete")

    def update_record_status_matrix(
        self, data, synthesized_record_status_matrix, endpoint_identifier
    ):

        # Note : automatically set all to True / synthesized
        for syn_id in list(synthesized_record_status_matrix.keys()):
            synthesized_record_status_matrix[syn_id][endpoint_identifier] = True


@dataclass
class ZettlrSettings:
    name: str
    zettlr_endpoint_version: str
    config: dict


@zope.interface.implementer(colrev.process.DataEndpoint)
class ZettlrEndpoint:

    NEW_RECORD_SOURCE_TAG = "<!-- NEW_RECORD_SOURCE -->"

    def __init__(self, *, data, settings):
        self.settings = from_dict(data_class=ZettlrSettings, data=settings)

    def get_default_setup(self):
        zettlr_endpoint_details = {
            "endpoint": "ZETTLR",
            "zettlr_endpoint_version": "0.1",
            "config": {},
        }
        return zettlr_endpoint_details

    def update_data(self, data, records: dict, synthesized_record_status_matrix: dict):

        data.review_manager.logger.info("Export to zettlr endpoint")

        endpoint_path = data.review_manager.path / Path("data/zettlr")

        # TODO : check if a main-zettlr file exists.

        def get_zettlr_missing(endpoint_path, included):
            in_zettelkasten = []

            for md_file in endpoint_path.glob("*.md"):
                with open(md_file, encoding="utf-8") as file:
                    line = file.readline()
                    while line:
                        if "title:" in line:
                            paper_id = line[line.find('"') + 1 : line.rfind('"')]
                            in_zettelkasten.append(paper_id)
                        line = file.readline()

            return [x for x in included if x not in in_zettelkasten]

        def add_missing_records_to_manuscript(
            *, review_manager, PAPER: Path, missing_records: list
        ):
            # pylint: disable=consider-using-with
            temp = tempfile.NamedTemporaryFile()
            PAPER.rename(temp.name)
            with open(temp.name, encoding="utf-8") as reader, open(
                PAPER, "w", encoding="utf-8"
            ) as writer:
                appended, completed = False, False
                line = reader.readline()
                while line != "":
                    if self.NEW_RECORD_SOURCE_TAG in line:
                        if "_Records to synthesize" not in line:
                            line = "_Records to synthesize_:" + line + "\n"
                            writer.write(line)
                        else:
                            writer.write(line)
                            writer.write("\n")

                        for missing_record in missing_records:
                            writer.write(missing_record)
                            review_manager.report_logger.info(
                                # f" {missing_record}".ljust(self.__PAD, " ")
                                f" {missing_record}"
                                + f" added to {PAPER.name}"
                            )

                            review_manager.logger.info(
                                # f" {missing_record}".ljust(self.__PAD, " ")
                                f" {missing_record}"
                                + f" added to {PAPER.name}"
                            )

                        # skip empty lines between to connect lists
                        line = reader.readline()
                        if "\n" != line:
                            writer.write(line)

                        appended = True

                    elif appended and not completed:
                        if "- @" == line[:3]:
                            writer.write(line)
                        else:
                            if "\n" != line:
                                writer.write("\n")
                            writer.write(line)
                            completed = True
                    else:
                        writer.write(line)
                    line = reader.readline()

                if not appended:
                    msg = (
                        f"Marker {self.NEW_RECORD_SOURCE_TAG} not found in "
                        + f"{PAPER.name}. Adding records at the end of "
                        + "the document."
                    )
                    review_manager.report_logger.warning(msg)
                    review_manager.logger.warning(msg)

                    if line != "\n":
                        writer.write("\n")
                    marker = f"{self.NEW_RECORD_SOURCE_TAG}_Records to synthesize_:\n\n"
                    writer.write(marker)
                    for missing_record in missing_records:
                        writer.write(missing_record)
                        review_manager.report_logger.info(
                            # f" {missing_record}".ljust(self.__PAD, " ") + " added"
                            f" {missing_record} added"
                        )
                        review_manager.logger.info(
                            # f" {missing_record}".ljust(self.__PAD, " ") + " added"
                            f" {missing_record} added"
                        )

        zettlr_config_path = endpoint_path / Path(".zettlr_config.ini")
        current_dt = datetime.datetime.now()
        if zettlr_config_path.is_file():
            zettlr_config = configparser.ConfigParser()
            zettlr_config.read(zettlr_config_path)
            zettlr_path = endpoint_path / Path(zettlr_config.get("general", "main"))

        else:

            unique_timestamp = current_dt + datetime.timedelta(seconds=3)
            zettlr_resource_path = Path("template/zettlr/") / Path("zettlr.md")
            fname = Path(unique_timestamp.strftime("%Y%m%d%H%M%S") + ".md")
            zettlr_path = endpoint_path / fname

            zettlr_config = configparser.ConfigParser()
            zettlr_config.add_section("general")
            zettlr_config["general"]["main"] = str(fname)
            with open(zettlr_config_path, "w", encoding="utf-8") as configfile:
                zettlr_config.write(configfile)
            data.review_manager.dataset.add_changes(path=str(zettlr_config_path))

            data.review_manager.retrieve_package_file(
                template_file=zettlr_resource_path, target=zettlr_path
            )
            title = "PROJECT_NAME"
            readme_file = data.review_manager.paths["README"]
            if readme_file.is_file():
                with open(readme_file, encoding="utf-8") as file:
                    title = file.readline()
                    title = title.replace("# ", "").replace("\n", "")

            data.review_manager.dataset.inplace_change(
                filename=zettlr_path, old_string="{{project_title}}", new_string=title
            )
            # author = authorship_heuristic(review_manager)
            data.review_manager.create_commit(
                msg="Add zettlr endpoint", script_call="colrev data"
            )

        records_dict = data.review_manager.dataset.load_records_dict()

        included = data.get_record_ids_for_synthesis(records_dict)

        missing_records = get_zettlr_missing(endpoint_path, included)

        if len(missing_records) == 0:
            print("All records included. Nothing to export.")
        else:
            print(missing_records)

            missing_records = sorted(missing_records)
            missing_record_fields = []
            for i, missing_record in enumerate(missing_records):
                unique_timestamp = current_dt - datetime.timedelta(seconds=i)
                missing_record_fields.append(
                    [unique_timestamp.strftime("%Y%m%d%H%M%S") + ".md", missing_record]
                )

            add_missing_records_to_manuscript(
                review_manager=data.review_manager,
                PAPER=zettlr_path,
                missing_records=[
                    "\n- [[" + i + "]] @" + r + "\n" for i, r in missing_record_fields
                ],
            )

            data.review_manager.dataset.add_changes(path=str(zettlr_path))

            zettlr_resource_path = Path("template/zettlr/") / Path("zettlr_bib_item.md")

            for missing_record_field in missing_record_fields:
                paper_id, record_field = missing_record_field
                print(paper_id + record_field)
                zettlr_path = endpoint_path / Path(paper_id)

                data.review_manager.retrieve_package_file(
                    template_file=zettlr_resource_path, target=zettlr_path
                )
                data.review_manager.dataset.inplace_change(
                    filename=zettlr_path,
                    old_string="{{project_name}}",
                    new_string=record_field,
                )
                with zettlr_path.open("a") as file:
                    file.write(f"\n\n@{record_field}\n")

                data.review_manager.dataset.add_changes(path=str(zettlr_path))

            data.review_manager.create_commit(
                msg="Setup zettlr", script_call="colrev data"
            )

            print("TODO: recommend zettlr/snippest, adding tags")

    def update_record_status_matrix(
        self, data, synthesized_record_status_matrix, endpoint_identifier
    ):
        # TODO : not yet implemented!
        # TODO : records mentioned after the NEW_RECORD_SOURCE tag are not synthesized.

        # Note : automatically set all to True / synthesized
        for syn_id in list(synthesized_record_status_matrix.keys()):
            synthesized_record_status_matrix[syn_id][endpoint_identifier] = True


class ManuscriptRecordSourceTagError(Exception):
    """NEW_RECORD_SOURCE_TAG not found in paper.md"""

    def __init__(self, msg):
        self.message = f" {msg}"
        super().__init__(self.message)
