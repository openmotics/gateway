# Copyright (C) 2020 OpenMotics BV
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Feedback LED DTO
"""
from gateway.dto.base_dto import BaseDTO


class FeedbackLedDTO(BaseDTO):

    class Functions(object):
        UNKNOWN = 'UNKNOWN'
        ON_B1_NORMAL = 'On B1'
        ON_B1_INVERTED = 'On B1 Inverted'
        FB_B1_NORMAL = 'Fast blink B1'
        FB_B1_INVERTED = 'Fast blink B1 Inverted'
        MB_B1_NORMAL = 'Medium blink B1'
        MB_B1_INVERTED = 'Medium blink B1 Inverted'
        SB_B1_NORMAL = 'Slow blink B1'
        SB_B1_INVERTED = 'Slow blink B1 Inverted'
        SW_B1_NORMAL = 'Swinging B1'
        SW_B1_INVERTED = 'Swinging B1 Inverted'
        ON_B2_NORMAL = 'On B2'
        ON_B2_INVERTED = 'On B2 Inverted'
        FB_B2_NORMAL = 'Fast blink B2'
        FB_B2_INVERTED = 'Fast blink B2 Inverted'
        MB_B2_NORMAL = 'Medium blink B2'
        MB_B2_INVERTED = 'Medium blink B2 Inverted'
        SB_B2_NORMAL = 'Slow blink B2'
        SB_B2_INVERTED = 'Slow blink B2 Inverted'
        SW_B2_NORMAL = 'Swinging B2'
        SW_B2_INVERTED = 'Swinging B2 Inverted'
        ON_B3_NORMAL = 'On B3'
        ON_B3_INVERTED = 'On B3 Inverted'
        FB_B3_NORMAL = 'Fast blink B3'
        FB_B3_INVERTED = 'Fast blink B3 Inverted'
        MB_B3_NORMAL = 'Medium blink B3'
        MB_B3_INVERTED = 'Medium blink B3 Inverted'
        SB_B3_NORMAL = 'Slow blink B3'
        SB_B3_INVERTED = 'Slow blink B3 Inverted'
        SW_B3_NORMAL = 'Swinging B3'
        SW_B3_INVERTED = 'Swinging B3 Inverted'
        ON_B4_NORMAL = 'On B4'
        ON_B4_INVERTED = 'On B4 Inverted'
        FB_B4_NORMAL = 'Fast blink B4'
        FB_B4_INVERTED = 'Fast blink B4 Inverted'
        MB_B4_NORMAL = 'Medium blink B4'
        MB_B4_INVERTED = 'Medium blink B4 Inverted'
        SB_B4_NORMAL = 'Slow blink B4'
        SB_B4_INVERTED = 'Slow blink B4 Inverted'
        SW_B4_NORMAL = 'Swinging B4'
        SW_B4_INVERTED = 'Swinging B4 Inverted'
        ON_B5_NORMAL = 'On B5'
        ON_B5_INVERTED = 'On B5 Inverted'
        FB_B5_NORMAL = 'Fast blink B5'
        FB_B5_INVERTED = 'Fast blink B5 Inverted'
        MB_B5_NORMAL = 'Medium blink B5'
        MB_B5_INVERTED = 'Medium blink B5 Inverted'
        SB_B5_NORMAL = 'Slow blink B5'
        SB_B5_INVERTED = 'Slow blink B5 Inverted'
        SW_B5_NORMAL = 'Swinging B5'
        SW_B5_INVERTED = 'Swinging B5 Inverted'
        ON_B6_NORMAL = 'On B6'
        ON_B6_INVERTED = 'On B6 Inverted'
        FB_B6_NORMAL = 'Fast blink B6'
        FB_B6_INVERTED = 'Fast blink B6 Inverted'
        MB_B6_NORMAL = 'Medium blink B6'
        MB_B6_INVERTED = 'Medium blink B6 Inverted'
        SB_B6_NORMAL = 'Slow blink B6'
        SB_B6_INVERTED = 'Slow blink B6 Inverted'
        SW_B6_NORMAL = 'Swinging B6'
        SW_B6_INVERTED = 'Swinging B6 Inverted'
        ON_B7_NORMAL = 'On B7'
        ON_B7_INVERTED = 'On B7 Inverted'
        FB_B7_NORMAL = 'Fast blink B7'
        FB_B7_INVERTED = 'Fast blink B7 Inverted'
        MB_B7_NORMAL = 'Medium blink B7'
        MB_B7_INVERTED = 'Medium blink B7 Inverted'
        SB_B7_NORMAL = 'Slow blink B7'
        SB_B7_INVERTED = 'Slow blink B7 Inverted'
        SW_B7_NORMAL = 'Swinging B7'
        SW_B7_INVERTED = 'Swinging B7 Inverted'
        ON_B8_NORMAL = 'On B8'
        ON_B8_INVERTED = 'On B8 Inverted'
        FB_B8_NORMAL = 'Fast blink B8'
        FB_B8_INVERTED = 'Fast blink B8 Inverted'
        MB_B8_NORMAL = 'Medium blink B8'
        MB_B8_INVERTED = 'Medium blink B8 Inverted'
        SB_B8_NORMAL = 'Slow blink B8'
        SB_B8_INVERTED = 'Slow blink B8 Inverted'
        SW_B8_NORMAL = 'Swinging B8'
        SW_B8_INVERTED = 'Swinging B8 Inverted'
        ON_B9_NORMAL = 'On B9'
        ON_B9_INVERTED = 'On B9 Inverted'
        FB_B9_NORMAL = 'Fast blink B9'
        FB_B9_INVERTED = 'Fast blink B9 Inverted'
        MB_B9_NORMAL = 'Medium blink B9'
        MB_B9_INVERTED = 'Medium blink B9 Inverted'
        SB_B9_NORMAL = 'Slow blink B9'
        SB_B9_INVERTED = 'Slow blink B9 Inverted'
        SW_B9_NORMAL = 'Swinging B9'
        SW_B9_INVERTED = 'Swinging B9 Inverted'
        ON_B10_NORMAL = 'On B10'
        ON_B10_INVERTED = 'On B10 Inverted'
        FB_B10_NORMAL = 'Fast blink B10'
        FB_B10_INVERTED = 'Fast blink B10 Inverted'
        MB_B10_NORMAL = 'Medium blink B10'
        MB_B10_INVERTED = 'Medium blink B10 Inverted'
        SB_B10_NORMAL = 'Slow blink B10'
        SB_B10_INVERTED = 'Slow blink B10 Inverted'
        SW_B10_NORMAL = 'Swinging B10'
        SW_B10_INVERTED = 'Swinging B10 Inverted'
        ON_B11_NORMAL = 'On B11'
        ON_B11_INVERTED = 'On B11 Inverted'
        FB_B11_NORMAL = 'Fast blink B11'
        FB_B11_INVERTED = 'Fast blink B11 Inverted'
        MB_B11_NORMAL = 'Medium blink B11'
        MB_B11_INVERTED = 'Medium blink B11 Inverted'
        SB_B11_NORMAL = 'Slow blink B11'
        SB_B11_INVERTED = 'Slow blink B11 Inverted'
        SW_B11_NORMAL = 'Swinging B11'
        SW_B11_INVERTED = 'Swinging B11 Inverted'
        ON_B12_NORMAL = 'On B12'
        ON_B12_INVERTED = 'On B12 Inverted'
        FB_B12_NORMAL = 'Fast blink B12'
        FB_B12_INVERTED = 'Fast blink B12 Inverted'
        MB_B12_NORMAL = 'Medium blink B12'
        MB_B12_INVERTED = 'Medium blink B12 Inverted'
        SB_B12_NORMAL = 'Slow blink B12'
        SB_B12_INVERTED = 'Slow blink B12 Inverted'
        SW_B12_NORMAL = 'Swinging B12'
        SW_B12_INVERTED = 'Swinging B12 Inverted'
        ON_B13_NORMAL = 'On B13'
        ON_B13_INVERTED = 'On B13 Inverted'
        FB_B13_NORMAL = 'Fast blink B13'
        FB_B13_INVERTED = 'Fast blink B13 Inverted'
        MB_B13_NORMAL = 'Medium blink B13'
        MB_B13_INVERTED = 'Medium blink B13 Inverted'
        SB_B13_NORMAL = 'Slow blink B13'
        SB_B13_INVERTED = 'Slow blink B13 Inverted'
        SW_B13_NORMAL = 'Swinging B13'
        SW_B13_INVERTED = 'Swinging B13 Inverted'
        ON_B14_NORMAL = 'On B14'
        ON_B14_INVERTED = 'On B14 Inverted'
        FB_B14_NORMAL = 'Fast blink B14'
        FB_B14_INVERTED = 'Fast blink B14 Inverted'
        MB_B14_NORMAL = 'Medium blink B14'
        MB_B14_INVERTED = 'Medium blink B14 Inverted'
        SB_B14_NORMAL = 'Slow blink B14'
        SB_B14_INVERTED = 'Slow blink B14 Inverted'
        SW_B14_NORMAL = 'Swinging B14'
        SW_B14_INVERTED = 'Swinging B14 Inverted'
        ON_B15_NORMAL = 'On B15'
        ON_B15_INVERTED = 'On B15 Inverted'
        FB_B15_NORMAL = 'Fast blink B15'
        FB_B15_INVERTED = 'Fast blink B15 Inverted'
        MB_B15_NORMAL = 'Medium blink B15'
        MB_B15_INVERTED = 'Medium blink B15 Inverted'
        SB_B15_NORMAL = 'Slow blink B15'
        SB_B15_INVERTED = 'Slow blink B15 Inverted'
        SW_B15_NORMAL = 'Swinging B15'
        SW_B15_INVERTED = 'Swinging B15 Inverted'
        ON_B16_NORMAL = 'On B16'
        ON_B16_INVERTED = 'On B16 Inverted'
        FB_B16_NORMAL = 'Fast blink B16'
        FB_B16_INVERTED = 'Fast blink B16 Inverted'
        MB_B16_NORMAL = 'Medium blink B16'
        MB_B16_INVERTED = 'Medium blink B16 Inverted'
        SB_B16_NORMAL = 'Slow blink B16'
        SB_B16_INVERTED = 'Slow blink B16 Inverted'
        SW_B16_NORMAL = 'Swinging B16'
        SW_B16_INVERTED = 'Swinging B16 Inverted'

    id = None  # type: None or int
    function = None  # type: Functions

    def __init__(self, id, function):  # type: (None or int, Functions) -> None
        self.id = id
        self.function = function

    @staticmethod
    def read_from_core_orm(core_object):
        raise NotImplementedError()

    @staticmethod
    def read_from_classic_orm(classic_object):
        raise NotImplementedError()


