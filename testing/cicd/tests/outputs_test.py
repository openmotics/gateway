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
from __future__ import absolute_import
import logging
import time

import hypothesis
import pytest
import ujson as json
from hypothesis.strategies import booleans, composite, integers, just, one_of
from six.moves import map

logger = logging.getLogger('openmotics')

DEFAULT_OUTPUT_CONFIG = {'type': 0, 'timer': 2**16 - 1}
DEFAULT_LIGHT_CONFIG = {'type': 255, 'timer': 2**16 - 1}


@composite
def next_output(draw):
    used_values = []
    def f(toolbox):
        value = draw(one_of(list(map(just, toolbox.dut_outputs))).filter(lambda x: x not in used_values))
        used_values.append(value)
        hypothesis.note('module o#{}'.format(value))
        return value
    return f


@pytest.mark.smoke
@hypothesis.given(next_output(), booleans())
def test_events(toolbox, next_output, output_status):
    output_id = next_output(toolbox)
    logger.info('output status o#{}, expect event {} -> {}'.format(output_id, not output_status, output_status))
    toolbox.ensure_output(output_id, not output_status, DEFAULT_OUTPUT_CONFIG)

    toolbox.set_output(output_id, output_status)
    toolbox.assert_output_changed(output_id, output_status)


@pytest.mark.smoke
@pytest.mark.skip(reason='fails consistently when running with the ci profile')
@hypothesis.given(next_output(), booleans())
def test_status(toolbox, next_output, output_status):
    output_id = next_output(toolbox)
    logger.info('output status o#{}, expect status ? -> {}'.format(output_id, output_status))
    toolbox.configure_output(output_id, DEFAULT_OUTPUT_CONFIG)

    toolbox.set_output(output_id, output_status)
    toolbox.assert_output_status(output_id, output_status)


@pytest.mark.smoke
@hypothesis.given(next_output(), just(True))
def test_timers(toolbox, next_output, output_status):
    output_id = next_output(toolbox)
    logger.info('output timer o#{}, expect event {} -> {}'.format(output_id, output_status, not output_status))
    output_config = {'type': 0, 'timer': 5}  # FIXME: event reordering with timer of <2s
    toolbox.ensure_output(output_id, False, output_config)

    toolbox.set_output(output_id, output_status)
    toolbox.assert_output_changed(output_id, output_status)
    toolbox.assert_output_changed(output_id, not output_status, between=(3, 7))


@pytest.mark.smoke
@hypothesis.given(next_output(), integers(min_value=0, max_value=254), just(True))
def test_floor_lights(toolbox, next_output, floor_id, output_status):
    light_id, other_light_id, other_output_id = (next_output(toolbox), next_output(toolbox), next_output(toolbox))
    logger.info('light o#{} on floor {}, expect event {} -> {}'.format(light_id, floor_id, not output_status, output_status))

    output_config = {'floor': floor_id}
    output_config.update(DEFAULT_LIGHT_CONFIG)
    toolbox.ensure_output(light_id, not output_status, output_config)
    output_config = {'floor': 255}  # no floor
    output_config.update(DEFAULT_LIGHT_CONFIG)
    toolbox.ensure_output(other_light_id, not output_status, output_config)
    output_config = {'floor': floor_id}
    output_config.update(DEFAULT_OUTPUT_CONFIG)  # not a light
    toolbox.ensure_output(other_output_id, not output_status, output_config)
    time.sleep(2)

    logger.info('enable all lights on floor {}'.format(floor_id))
    toolbox.dut.get('/set_all_lights_floor_on', params={'floor': floor_id})
    toolbox.assert_output_changed(light_id, output_status)
    toolbox.assert_output_status(other_light_id, not output_status)
    toolbox.assert_output_status(other_output_id, not output_status)

    logger.info('disable all lights on floor {}'.format(floor_id))
    toolbox.dut.get('/set_all_lights_floor_off', params={'floor': floor_id})
    toolbox.assert_output_changed(light_id, not output_status)
    toolbox.assert_output_status(other_light_id, not output_status)
    toolbox.assert_output_status(other_output_id, not output_status)


@pytest.mark.smoke
@pytest.mark.skip(reason='fails consistently when running with the ci profile')
@hypothesis.given(next_output(), integers(min_value=0, max_value=159), booleans())
def test_group_action_toggle(toolbox, next_output, group_action_id, output_status):
    (output_id, other_output_id) = (next_output(toolbox), next_output(toolbox))
    logger.info('group action a#{} for o#{} o#{}, expect event {} -> {}'.format(group_action_id, output_id, other_output_id, not output_status, output_status))

    actions = ['162', str(output_id), '162', str(other_output_id)]  # toggle both outputs
    config = {'id': group_action_id, 'actions': ','.join(actions)}
    toolbox.dut.get('/set_group_action_configuration', params={'config': json.dumps(config)})
    time.sleep(2)

    toolbox.ensure_output(output_id, not output_status, DEFAULT_OUTPUT_CONFIG)
    toolbox.ensure_output(other_output_id, not output_status, DEFAULT_OUTPUT_CONFIG)

    toolbox.dut.get('/do_group_action', {'group_action_id': group_action_id})
    toolbox.assert_output_changed(output_id, output_status)
    toolbox.assert_output_changed(other_output_id, output_status)
