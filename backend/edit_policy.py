"""One script-led editing policy; legacy requests normalize to the same rules."""
import math

REVISION = 'edit-policy-3'
# Retrieval and required script coverage are independent of editorial pacing.
# These keys remain public for old callers, but must not diverge by mode.
CONTENT_POLICY = dict(minScore=.52, extraTextPenalty=.04,
                      requiredJoinGap=1.2, fallback=False)
POLICIES = {'script': dict(label='按剧本剪辑', pre=.12, post=.35, eventTail=.8,
    dialogueGap=.45, visualBefore=.25, visualAfter=.8, settle=.2, continuation=0)}
LEGACY_MODES = frozenset(('strict', 'balanced', 'expressive'))


def policy(mode=None):
    if mode is None or isinstance(mode,str) and mode in LEGACY_MODES:
        mode = 'script'
    if not isinstance(mode, str) or mode not in POLICIES:
        raise ValueError('剪辑设置无效；当前统一按剧本剪辑，请刷新工作台后重试')
    return dict(POLICIES[mode], **CONTENT_POLICY, mode=mode, revision=REVISION)


def cap_visual_extension(row, suggestion, selected_policy, fps):
    """Cap only extra visual handles; never shorten an already selected unit."""
    a, b = row['start'], row['end']
    start = max(a-selected_policy['visualBefore'], min(a, suggestion[0]))
    end = min(b+selected_policy['visualAfter'], max(b, suggestion[1]))
    return min(a, math.ceil((start-1e-8)*fps)/fps), max(b, math.floor((end+1e-8)*fps)/fps)
