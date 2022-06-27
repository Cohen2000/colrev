#! /usr/bin/env python
import csv
import typing
from pathlib import Path

import pandas as pd

from colrev_core.process import Process
from colrev_core.process import ProcessType
from colrev_core.record import Record
from colrev_core.record import RecordState


class PrescreenRecord(Record):
    def __init__(self, *, data: dict):
        super().__init__(data=data)

    def __str__(self) -> str:

        self.identifying_keys_order = ["ID", "ENTRYTYPE"] + [
            k for k in self.identifying_field_keys if k in self.data
        ]
        complementary_keys_order = [
            k for k, v in self.data.items() if k not in self.identifying_keys_order
        ]

        ik_sorted = {
            k: v for k, v in self.data.items() if k in self.identifying_keys_order
        }
        ck_sorted = {
            k: v
            for k, v in self.data.items()
            if k in complementary_keys_order and k not in self.provenance_keys
        }
        ret_str = (
            self.pp.pformat(ik_sorted)[:-1] + "\n" + self.pp.pformat(ck_sorted)[1:]
        )

        return ret_str


class Prescreen(Process):
    def __init__(self, *, REVIEW_MANAGER, notify_state_transition_process: bool = True):
        super().__init__(
            REVIEW_MANAGER=REVIEW_MANAGER,
            type=ProcessType.prescreen,
            notify_state_transition_process=notify_state_transition_process,
        )

    def export_table(self, *, export_table_format: str) -> None:
        self.REVIEW_MANAGER.logger.info("Loading records for export")
        records = self.REVIEW_MANAGER.REVIEW_DATASET.load_records_dict()

        tbl = []
        for record in records.vaules():

            if record["colrev_status"] in [
                RecordState.md_imported,
                RecordState.md_retrieved,
                RecordState.md_needs_manual_preparation,
                RecordState.md_prepared,
            ]:
                continue

            inclusion_1, inclusion_2 = "NA", "NA"

            if RecordState.md_processed == record["colrev_status"]:
                inclusion_1 = "TODO"
            elif RecordState.rev_prescreen_excluded == record["colrev_status"]:
                inclusion_1 = "no"
            else:
                inclusion_1 = "yes"
                inclusion_2 = "TODO"
                if RecordState.rev_excluded == record["colrev_status"]:
                    inclusion_2 = "no"
                if record["colrev_status"] in [
                    RecordState.rev_included,
                    RecordState.rev_synthesized,
                ]:
                    inclusion_2 = "yes"

            exclusion_criteria = record.get("exclusion_criteria", "NA")
            if exclusion_criteria == "NA" and inclusion_2 == "yes":
                exclusion_criteria = "TODO"

            row = {
                "ID": record["ID"],
                "author": record.get("author", ""),
                "title": record.get("title", ""),
                "journal": record.get("journal", ""),
                "booktitle": record.get("booktitle", ""),
                "year": record.get("year", ""),
                "volume": record.get("volume", ""),
                "number": record.get("number", ""),
                "pages": record.get("pages", ""),
                "doi": record.get("doi", ""),
                "abstract": record.get("abstract", ""),
                "inclusion_1": inclusion_1,
                "inclusion_2": inclusion_2,
                "exclusion_criteria": exclusion_criteria,
            }
            # row.update    (exclusion_criteria)
            tbl.append(row)

        if "csv" == export_table_format.lower():
            screen_df = pd.DataFrame(tbl)
            screen_df.to_csv("screen_table.csv", index=False, quoting=csv.QUOTE_ALL)
            self.REVIEW_MANAGER.logger.info("Created screen_table (csv)")

        if "xlsx" == export_table_format.lower():
            screen_df = pd.DataFrame(tbl)
            screen_df.to_excel("screen_table.xlsx", index=False, sheet_name="screen")
            self.REVIEW_MANAGER.logger.info("Created screen_table (xlsx)")

        return

    def import_table(self, *, import_table_path: str) -> None:

        records = self.REVIEW_MANAGER.REVIEW_DATASET.load_records_dict()
        if not Path(import_table_path).is_file():
            self.REVIEW_MANAGER.logger.error(
                f"Did not find {import_table_path} - exiting."
            )
            return
        screen_df = pd.read_csv(import_table_path)
        screen_df.fillna("", inplace=True)
        screened_records = screen_df.to_dict("records")

        self.REVIEW_MANAGER.logger.warning(
            "import_table not completed (exclusion_criteria not yet imported)"
        )

        for screened_record in screened_records:
            if screened_record.get("ID", "") in records:
                record = records[screened_record.get("ID", "")]
                if "no" == screened_record.get("inclusion_1", ""):
                    record["colrev_status"] = RecordState.rev_prescreen_excluded
                if "yes" == screened_record.get("inclusion_1", ""):
                    record["colrev_status"] = RecordState.rev_prescreen_included
                if "no" == screened_record.get("inclusion_2", ""):
                    record["colrev_status"] = RecordState.rev_excluded
                if "yes" == screened_record.get("inclusion_2", ""):
                    record["colrev_status"] = RecordState.rev_included
                if "" != screened_record.get("exclusion_criteria", ""):
                    record["exclusion_criteria"] = screened_record.get(
                        "exclusion_criteria", ""
                    )

        self.REVIEW_MANAGER.REVIEW_DATASET.save_records_dict(records=records)

        return

    def include_all_in_prescreen(self) -> None:

        records = self.REVIEW_MANAGER.REVIEW_DATASET.load_records_dict()

        saved_args = locals()
        saved_args["include_all"] = ""
        PAD = 50
        for record in records.values():
            if record["colrev_status"] != RecordState.md_processed:
                continue
            self.REVIEW_MANAGER.report_logger.info(
                f' {record["ID"]}'.ljust(PAD, " ")
                + "Included in prescreen (automatically)"
            )
            record.update(colrev_status=RecordState.rev_prescreen_included)

        self.REVIEW_MANAGER.REVIEW_DATASET.save_records_dict(records=records)
        self.REVIEW_MANAGER.REVIEW_DATASET.add_record_changes()
        self.REVIEW_MANAGER.create_commit(
            msg="Pre-screen (include_all)", manual_author=False, saved_args=saved_args
        )

        return

    def run_scope_based_prescreen(self) -> None:
        from colrev_core.settings import (
            TimeScopeFrom,
            TimeScopeTo,
            OutletInclusionScope,
            OutletExclusionScope,
            ENTRYTYPEScope,
            ComplementaryMaterialsScope,
        )

        def load_predatory_journals_beal() -> dict:

            import pkgutil

            predatory_journals = {}

            filedata = pkgutil.get_data(
                __name__, "template/predatory_journals_beall.csv"
            )
            if filedata:
                for pj in filedata.decode("utf-8").splitlines():
                    predatory_journals[pj.lower()] = pj.lower()

            return predatory_journals

        self.predatory_journals_beal = load_predatory_journals_beal()

        records = self.REVIEW_MANAGER.REVIEW_DATASET.load_records_dict()

        saved_args = locals()
        PAD = 50
        for record in records.values():
            if record["colrev_status"] != RecordState.md_processed:
                continue

            # Note : LanguageScope is covered in prep
            # because dedupe cannot handle merges between languages

            for scope_restriction in self.REVIEW_MANAGER.settings.prescreen.scope:

                if isinstance(scope_restriction, ENTRYTYPEScope):
                    if record["ENTRYTYPE"] not in scope_restriction.ENTRYTYPEScope:
                        Record(data=record).prescreen_exclude(
                            reason="not in ENTRYTYPEScope"
                        )

                if isinstance(scope_restriction, OutletExclusionScope):
                    if "values" in scope_restriction.OutletExclusionScope:
                        for r in scope_restriction.OutletExclusionScope["values"]:
                            for key, value in r.items():
                                if key in record and record.get(key, "") == value:
                                    Record(data=record).prescreen_exclude(
                                        reason="in OutletExclusionScope"
                                    )
                    if "list" in scope_restriction.OutletExclusionScope:
                        for r in scope_restriction.OutletExclusionScope["list"]:
                            for key, value in r.items():
                                if (
                                    "resource" == key
                                    and "predatory_journals_beal" == value
                                ):
                                    if "journal" in record:
                                        if (
                                            record["journal"].lower()
                                            in self.predatory_journals_beal
                                        ):
                                            Record(data=record).prescreen_exclude(
                                                reason="predatory_journals_beal"
                                            )

                if isinstance(scope_restriction, TimeScopeFrom):
                    if int(record.get("year", 0)) < scope_restriction.TimeScopeFrom:
                        Record(data=record).prescreen_exclude(
                            reason="not in TimeScopeFrom "
                            f"(>{scope_restriction.TimeScopeFrom})"
                        )

                if isinstance(scope_restriction, TimeScopeTo):
                    if int(record.get("year", 5000)) > scope_restriction.TimeScopeTo:
                        Record(data=record).prescreen_exclude(
                            reason="not in TimeScopeTo "
                            f"(<{scope_restriction.TimeScopeTo})"
                        )

                if isinstance(scope_restriction, OutletInclusionScope):
                    in_outlet_scope = False
                    if "values" in scope_restriction.OutletInclusionScope:
                        for r in scope_restriction.OutletInclusionScope["values"]:
                            for key, value in r.items():
                                if key in record and record.get(key, "") == value:
                                    in_outlet_scope = True
                    if not in_outlet_scope:
                        Record(data=record).prescreen_exclude(
                            reason="not in OutletInclusionScope"
                        )

                # TODO : discuss whether we should move this to the prep scripts
                if isinstance(scope_restriction, ComplementaryMaterialsScope):
                    if scope_restriction.ComplementaryMaterialsScope:
                        if "title" in record:
                            # TODO : extend/test the following
                            if record["title"].lower() in [
                                "about our authors",
                                "editorial board",
                                "author index",
                                "contents",
                                "index of authors",
                                "list of reviewers",
                            ]:
                                Record(data=record).prescreen_exclude(
                                    reason="complementary material"
                                )

            if record["colrev_status"] != RecordState.rev_prescreen_excluded:
                self.REVIEW_MANAGER.report_logger.info(
                    f' {record["ID"]}'.ljust(PAD, " ")
                    + "Prescreen excluded (automatically)"
                )

        self.REVIEW_MANAGER.REVIEW_DATASET.save_records_dict(records=records)
        self.REVIEW_MANAGER.REVIEW_DATASET.add_record_changes()
        self.REVIEW_MANAGER.create_commit(
            msg="Pre-screen (scope)", manual_author=False, saved_args=saved_args
        )

        return

    def get_data(self) -> dict:

        record_state_list = self.REVIEW_MANAGER.REVIEW_DATASET.get_record_state_list()
        nr_tasks = len(
            [
                x
                for x in record_state_list
                if str(RecordState.md_processed) == x["colrev_status"]
            ]
        )
        PAD = min((max(len(x["ID"]) for x in record_state_list) + 2), 40)
        items = self.REVIEW_MANAGER.REVIEW_DATASET.read_next_record(
            conditions=[{"colrev_status": RecordState.md_processed}]
        )
        prescreen_data = {"nr_tasks": nr_tasks, "PAD": PAD, "items": items}
        self.REVIEW_MANAGER.logger.debug(self.REVIEW_MANAGER.pp.pformat(prescreen_data))
        return prescreen_data

    def set_data(
        self, *, record: dict, prescreen_inclusion: bool, PAD: int = 40
    ) -> None:

        if prescreen_inclusion:
            self.REVIEW_MANAGER.report_logger.info(
                f" {record['ID']}".ljust(PAD, " ") + "Included in prescreen"
            )
            self.REVIEW_MANAGER.REVIEW_DATASET.replace_field(
                IDs=[record["ID"]],
                key="colrev_status",
                val_str=str(RecordState.rev_prescreen_included),
            )
        else:
            self.REVIEW_MANAGER.report_logger.info(
                f" {record['ID']}".ljust(PAD, " ") + "Excluded in prescreen"
            )
            self.REVIEW_MANAGER.REVIEW_DATASET.replace_field(
                IDs=[record["ID"]],
                key="colrev_status",
                val_str=str(RecordState.rev_prescreen_excluded),
            )

        self.REVIEW_MANAGER.REVIEW_DATASET.add_record_changes()

        return

    def create_prescreen_split(self, *, create_split: int) -> list:
        import math

        prescreen_splits = []

        data = self.get_data()
        nrecs = math.floor(data["nr_tasks"] / create_split)

        self.REVIEW_MANAGER.report_logger.info(
            f"Creating prescreen splits for {create_split} researchers "
            f"({nrecs} each)"
        )

        for i in range(0, create_split):
            added: typing.List[str] = []
            while len(added) < nrecs:
                added.append(next(data["items"])["ID"])
            prescreen_splits.append("colrev prescreen --split " + ",".join(added))

        return prescreen_splits


if __name__ == "__main__":
    pass
