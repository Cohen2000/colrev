#! /usr/bin/env python
import shutil
from pathlib import Path

import colrev.process
import colrev.settings


class Distribute(colrev.process.Process):
    def __init__(self, *, review_manager):
        super().__init__(
            review_manager=review_manager,
            process_type=colrev.process.ProcessType.explore,
            notify_state_transition_process=False,
        )

    def main(self, *, path_str: str, target: Path) -> None:

        # if no options are given, take the current path/repo
        # optional: target-repo-path
        # path_str: could also be a url
        # option: chdir (to target repo)?
        # file: copy or move?

        path = Path.cwd() / Path(path_str)
        if path.is_file():
            if path.suffix == ".pdf":

                # Note : this is actually correct: camel case for classes...
                grobid_service = self.review_manager.get_grobid_service()

                grobid_service.start()

                tei = self.review_manager.get_tei(
                    pdf_path=path,
                )
                record = tei.get_metadata()

                target_pdf_path = target / "pdfs" / path.name
                target_pdf_path.parent.mkdir(parents=True, exist_ok=True)
                self.review_manager.logger.info(f"Copy PDF to {target_pdf_path}")
                shutil.copyfile(path, target_pdf_path)

                self.review_manager.logger.info(
                    f"append {self.review_manager.p_printer.pformat(record)} "
                    "to search/local_import.bib"
                )
                target_bib_file = target / "search/local_import.bib"
                self.review_manager.logger.info(f"target_bib_file: {target_bib_file}")
                if target_bib_file.is_file():
                    with open(target_bib_file, encoding="utf8") as target_bib:
                        import_records_dict = (
                            self.review_manager.dataset.load_records_dict(
                                load_str=target_bib.read()
                            )
                        )
                        import_records = import_records_dict.values()
                else:
                    import_records = []

                    new_source = colrev.settings.SearchSource(
                        filename=Path("search") / target_bib_file.name,
                        search_type=colrev.settings.SearchType.OTHER,
                        source_name="locally_distributed_references",
                        source_identifier="",
                        search_parameters="",
                        search_script={},
                        conversion_script={},
                        source_prep_scripts=[{}],
                        comment="",
                    )

                    self.review_manager.settings.sources.append(new_source)
                    self.review_manager.save_settings()

                if 0 != len(import_records):
                    record_id = int(
                        self.review_manager.dataset.get_next_id(target_bib_file)
                    )

                record["ID"] = f"{record_id}".rjust(10, "0")
                record.update(file=str(target_pdf_path))
                import_records.append(record)

                import_records_dict = {r["ID"]: r for r in import_records}
                self.review_manager.dataset.save_records_dict_to_file(
                    import_records_dict, save_path=target_bib_file
                )

                self.review_manager.dataset.add_changes(path=str(target_bib_file))


if __name__ == "__main__":
    pass
