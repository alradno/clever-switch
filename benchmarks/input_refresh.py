"""Ideal sample/display phase sweep. This does not inspect hardware or input.

A report rate is a model input, never a measured mouse specification. The model
contains no transport, OS scheduling, loss or compositor stalls, so it isolates
only the effect of asynchronous reports and display presentation clocks.
"""

import json
import math


def phase_case(report_hz, display_hz, phase, seconds=2):
    previous = None
    updated_at = last_update = 0.0
    maximum_gap = maximum_step = repeats = 0
    for frame in range(int(seconds * display_hz) + 1):
        now = frame / display_hz
        report = math.floor((now + phase) * report_hz + 1e-9)
        if previous is not None:
            if report == previous:
                repeats += 1
            else:
                updated_at = now
                maximum_gap = max(maximum_gap, updated_at - last_update)
                maximum_step = max(maximum_step, report - previous)
                last_update = updated_at
        previous = report
    return maximum_gap * 1000, maximum_step, repeats


def run():
    cases = []
    for report_hz in (60, 90, 125, 250, 500, 1000):
        for display_hz in (60, 75, 90, 120, 144, 165, 240):
            phases = [phase_case(report_hz, display_hz, i / (64 * report_hz)) for i in range(64)]
            gap = max(value[0] for value in phases)
            bound = 1000 / report_hz + 1000 / display_hz
            if gap > bound + 1e-6:
                raise AssertionError('The sampled result exceeds the two-clock quantization bound')
            cases.append({
                'modeled_report_hz': report_hz, 'modeled_display_hz': display_hz,
                'phases': len(phases), 'maximum_presented_update_gap_ms': round(gap, 3),
                'maximum_reports_per_frame': max(value[1] for value in phases),
                'any_80ms_pause': gap >= 80,
            })
    return {
        'scope': 'ideal_continuous_motion_sampled_by_two_unsynchronized_clocks',
        'not_measured': ['actual_device_report_rate', 'Wi-Fi', 'capture_scheduling', 'event_injection',
                         'compositor', 'variable_refresh', 'screen_recording'],
        'interpretation': 'At the modeled rates, phase mismatch alone does not generate an 80-140ms pause. '
                          'It can change the movement increment per frame and repeat frames when reports are slower.',
        'cases': cases,
    }


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
