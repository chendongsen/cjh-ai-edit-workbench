"""Convert Resolve source times to file-relative seconds without losing subframes.

Resolve can include the media's timecode origin in GetSourceStartTime(), while
GetSourceStartFrame() is a coarse source-frame readback.  Do not substitute that
integer for the time value: linked audio may have a more precise source in-point.
"""
import math
import re
from fractions import Fraction


def _number(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'无法确认素材{label}：{value!r}') from None
    if not math.isfinite(result):
        raise ValueError(f'无法确认素材{label}：{value!r}')
    return result


def source_rate(value):
    rate = _number(value, '帧率')
    if rate <= 0:
        raise ValueError('素材帧率必须大于零')
    # Resolve displays these NTSC rates with limited decimal precision.  This
    # mapping deliberately does not snap arbitrary average/VFR rates to 60 fps.
    for display, numerator in ((23.976, 24000), (29.97, 30000),
                               (47.952, 48000), (59.94, 60000),
                               (119.88, 120000)):
        exact = Fraction(numerator, 1001)
        if abs(rate - display) < 1e-9 or abs(rate - float(exact)) < 1e-9:
            return exact
    return Fraction(str(value))


def timecode_origin(properties):
    """Return the media TC origin in seconds, using its source timebase only."""
    tc = str(properties.get('Start TC') or '').strip()
    if not tc:
        return 0.0
    match = re.fullmatch(r'(\d{2}):(\d{2}):(\d{2})([:;])(\d{2,3})', tc)
    if not match:
        raise ValueError('无法解析素材起始时间码：' + tc)
    hours, minutes, seconds, separator, frames = match.groups()
    hours, minutes, seconds, frames = map(int, (hours, minutes, seconds, frames))
    if hours > 23 or minutes > 59 or seconds > 59:
        raise ValueError('素材起始时间码超出范围：' + tc)
    # Zero TC is also used by audio-only files without a video FPS property.
    if not any((hours, minutes, seconds, frames)):
        return 0.0
    rate = source_rate(properties.get('FPS'))
    nominal = round(float(rate))
    if not 0 <= frames < nominal:
        raise ValueError('素材起始时间码帧号超出范围：' + tc)
    flag = str(properties.get('Drop frame', '')).strip().lower()
    if flag not in ('', '0', '1', 'false', 'true'):
        raise ValueError('无法确认素材丢帧时间码标记：' + flag)
    if separator == ';' and flag in ('0', 'false'):
        raise ValueError('素材时间码与丢帧标记不一致：' + tc)
    drop = separator == ';' or flag in ('1', 'true')
    count = ((hours * 3600 + minutes * 60 + seconds) * nominal + frames)
    if drop:
        if rate not in (Fraction(30000, 1001), Fraction(60000, 1001)):
            raise ValueError('不支持该素材帧率的丢帧时间码：' + tc)
        skipped = 2 if nominal == 30 else 4
        if minutes % 10 and seconds == 0 and frames < skipped:
            raise ValueError('素材时间码使用了被跳过的丢帧帧号：' + tc)
        total_minutes = hours * 60 + minutes
        count -= skipped * (total_minutes - total_minutes // 10)
    return float(Fraction(count, 1) / rate)


def relative_source_start(raw_time, source_frame, properties):
    """Normalize a source in-point; reject ambiguous timebases instead of guessing.

    The numeric media ``Start`` is a frame index, not the Start TC's seconds.
    It is used only to validate an otherwise out-of-range frame index.  It is
    never subtracted from GetSourceStartTime().
    """
    raw = _number(raw_time, '源入点时间')
    origin = timecode_origin(properties)
    fps_value = properties.get('FPS')
    rate = float(source_rate(fps_value)) if fps_value not in (None, '', 0, '0') else None
    frame = _number(source_frame, '源入点帧号') if source_frame is not None else None
    count_value = properties.get('Frames')
    count = _number(count_value, '总帧数') if count_value not in (None, '') else None
    start_value = properties.get('Start')
    start = _number(start_value, '起始帧号') if start_value not in (None, '') else 0.0
    if count is not None and count <= 0:
        count = None  # Still images/audio may have no meaningful video frame count.
    if frame is not None and count is not None and not 0 <= frame < count:
        if start and 0 <= frame - start < count:
            frame -= start
        else:
            raise ValueError('素材源入点帧号超出文件范围，无法确认源时间基')

    def valid(value):
        if value < -1e-7:
            return False
        if rate and count is not None and value * rate >= count + 1e-5:
            return False
        # Observed Resolve readbacks can differ by one source frame (e.g.
        # source frame 15 but source time 16/59.94).  Preserve the precise time.
        return not (rate and frame is not None) or abs(value * rate - frame) <= 1.00001

    if not origin:
        if not valid(raw):
            raise ValueError('素材源入点时间与源帧号不一致，无法确认源时间基')
        return max(0.0, raw)
    if rate is None or frame is None:
        raise ValueError('非零素材时间码缺少源帧率或入点帧号，无法安全转换')
    candidates = [value for value in (raw, raw - origin) if valid(value)]
    if len(candidates) != 1:
        raise ValueError('素材源时间基不明确，无法安全扣除起始时间码')
    return max(0.0, candidates[0])
