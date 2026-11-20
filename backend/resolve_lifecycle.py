"""Protect the current Resolve project before an intentional project switch.

This module deliberately does not import Resolve or create/close/delete projects.
The caller may create its new project only after this helper returns successfully.
"""

_UNTITLED = {'untitled project', 'untitled', '未命名项目', '未命名'}


def _name(project):
    try:
        name = project.GetName()
    except Exception as error:
        raise RuntimeError('无法读取当前达芬奇项目名称；未保存或切换项目') from error
    if not isinstance(name, str) or not name.strip():
        raise RuntimeError('当前达芬奇项目名称不明确；未保存或切换项目')
    return name


def _project_id(project):
    getter = getattr(project, 'GetUniqueId', None)
    if not callable(getter):
        return None
    try:
        identifier = getter()
    except Exception as error:
        raise RuntimeError('无法核对当前达芬奇项目身份；未切换项目') from error
    return identifier if isinstance(identifier, str) and identifier else None


def _ensure_current(pm, expected, expected_id, expected_name):
    try:
        current = pm.GetCurrentProject()
    except Exception as error:
        raise RuntimeError('无法核对当前达芬奇项目；未继续保存或切换') from error
    if current is None:
        raise RuntimeError('当前达芬奇项目已关闭；未继续保存或切换')
    # Resolve exposes a stable project ID. For simple adapters without that API,
    # require the same object; a matching name alone cannot prove identity.
    same = (_project_id(current) == expected_id if expected_id is not None
            else current is expected)
    if not same or _name(current) != expected_name:
        raise RuntimeError('保护期间达芬奇当前项目发生变化；未继续保存或切换')


def preserve_current(pm, new_project_name):
    """Save existing work without invoking unnamed-project Save As first.

    Recognized untitled names not present in the current library folder receive
    a separate, unique preservation name. Content is never inspected, discarded,
    or reused for the incoming edit. Any uncertain/failing operation blocks the
    caller; in particular SetName failure must never fall through to SaveProject.
    """
    try:
        current = pm.GetCurrentProject()
    except Exception as error:
        raise RuntimeError('无法读取达芬奇当前项目；未保存或切换项目') from error
    if current is None:
        return {'action': 'none', 'originalName': None, 'preservedName': None,
                'renamed': False, 'saved': False}

    original_name = _name(current)
    current_id = _project_id(current)
    try:
        names = pm.GetProjectListInCurrentFolder()
    except Exception as error:
        raise RuntimeError('无法读取达芬奇项目库，不能安全确认名称；未保存或切换项目') from error
    if not isinstance(names, (list, tuple)) or any(not isinstance(name, str) for name in names):
        raise RuntimeError('达芬奇项目库返回异常，不能安全确认名称；未保存或切换项目')

    known_names = {name.strip().casefold() for name in names}
    unnamed = original_name.strip().casefold() in _UNTITLED
    needs_name = unnamed and original_name.strip().casefold() not in known_names
    preserved_name = original_name
    _ensure_current(pm, current, current_id, original_name)
    if needs_name:
        if not isinstance(new_project_name, str) or not new_project_name.strip():
            raise RuntimeError('新粗剪项目名称为空，无法生成原项目保留名称；未保存或切换项目')
        base = new_project_name.strip() + '_原未命名项目保留'
        preserved_name = base
        suffix = 2
        while preserved_name.casefold() in known_names:
            preserved_name = f'{base}_{suffix}'
            suffix += 1
        try:
            renamed = current.SetName(preserved_name)
        except Exception as error:
            raise RuntimeError('无法为未命名项目设置保留名称；未调用保存或切换，原内容仍在当前项目') from error
        if renamed is not True:
            raise RuntimeError('未命名项目设置保留名称失败；未调用保存或切换，原内容仍在当前项目')
        if _name(current) != preserved_name:
            raise RuntimeError('未命名项目名称回读不一致；未调用保存或切换，原内容仍保留')

    _ensure_current(pm, current, current_id, preserved_name)
    try:
        saved = pm.SaveProject()
    except Exception as error:
        raise RuntimeError('当前达芬奇项目保存失败；未切换项目，当前内容保留，请检查项目状态') from error
    if saved is not True:
        raise RuntimeError('当前达芬奇项目保存失败；未切换项目，当前内容保留，请检查磁盘和项目状态')
    _ensure_current(pm, current, current_id, preserved_name)
    receipt = {'action': 'renamed-and-saved' if needs_name else 'saved-existing',
               'originalName': original_name, 'preservedName': preserved_name,
               'renamed': needs_name, 'saved': True}
    if current_id is not None:
        receipt['projectId'] = current_id
    return receipt
