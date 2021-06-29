from typing import Tuple

from common.common_consts.telem_categories import TelemCategoryEnum
from infection_monkey.telemetry.base_telem import BaseTelem
from infection_monkey.telemetry.batchable_telem_mixin import BatchableTelemMixin
from infection_monkey.telemetry.i_batchable_telem import IBatchableTelem


class RansomwareTelem(BatchableTelemMixin, IBatchableTelem, BaseTelem):
    def __init__(self, entry: Tuple[str, str]):
        """
        Ransomware telemetry constructor
        :param attempts: List of tuples with each tuple containing the path
                         of a file it tried encrypting and its result.
                         If ransomware fails completely - list of one tuple
                         containing the directory path and error string.
        """
        super().__init__()

        self._telemetry_entries.append(entry)

    telem_category = TelemCategoryEnum.RANSOMWARE

    def get_data(self):
        return {"ransomware_attempts": self._telemetry_entries}