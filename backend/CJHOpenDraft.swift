// Local Jianying handoff. No network, no forced quit, no permission auto-grant.
import AppKit
import ApplicationServices
import Foundation

func output(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}
func attr(_ element: AXUIElement, _ key: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success ? value : nil
}
func str(_ element: AXUIElement, _ key: String) -> String { attr(element, key) as? String ?? "" }
func children(_ element: AXUIElement) -> [AXUIElement] { attr(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] }
func rectangle(_ element: AXUIElement) -> CGRect? {
    guard let p = attr(element,kAXPositionAttribute), let s = attr(element,kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var point=CGPoint.zero; var size=CGSize.zero
    guard AXValueGetValue(p as! AXValue,.cgPoint,&point), AXValueGetValue(s as! AXValue,.cgSize,&size) else { return nil }
    return CGRect(origin:point,size:size)
}
let trusted = AXIsProcessTrusted()
let args = Array(CommandLine.arguments.dropFirst())
if args == ["--check"] {
    output(["trusted": trusted, "helper": CommandLine.arguments[0]])
    exit(0)
}
guard trusted else {
    output(["draftOpened": false, "openState": "permission-required", "needsUserAction": true,
            "note": "需要在系统设置 → 隐私与安全性 → 辅助功能授权本机草稿助手；草稿已保存，未模拟任何点击。",
            "helper": CommandLine.arguments[0]])
    exit(0)
}
func mainApp() -> NSRunningApplication? {
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: "com.lemon.lvpro").filter {
        $0.executableURL?.lastPathComponent == "VideoFusion-macOS" && $0.activationPolicy == .regular
    }
    return apps.count == 1 ? apps[0] : nil
}
var startingApp = mainApp()
for _ in 0..<32 {
    if startingApp != nil { break }
    RunLoop.current.run(until:Date().addingTimeInterval(0.25)); startingApp=mainApp()
}
guard let app = startingApp else {
    output(["draftOpened": false, "openState": "app-not-running"]); exit(0)
}
guard let appURL = app.bundleURL else { output(["draftOpened":false,"openState":"app-location-unknown"]); exit(0) }
let root = AXUIElementCreateApplication(app.processIdentifier)
AXUIElementSetMessagingTimeout(root, 2)
if args == ["--inspect"] {
    var rows: [[String: Any]] = []
    func walk(_ element: AXUIElement, _ depth: Int) {
        if depth > 16 || rows.count >= 2000 { return }
        let rect=rectangle(element) ?? .zero
        rows.append(["depth":depth, "role":str(element,kAXRoleAttribute), "title":str(element,kAXTitleAttribute),
                     "description":str(element,kAXDescriptionAttribute), "value":str(element,kAXValueAttribute),
                     "rect":[rect.minX,rect.minY,rect.width,rect.height], "children":children(element).count])
        for child in children(element) { walk(child, depth+1) }
    }
    for window in attr(root, kAXWindowsAttribute) as? [AXUIElement] ?? [] { walk(window, 0) }
    output(["elements":rows, "postEventsAllowed":CGPreflightPostEventAccess()]); exit(0)
}
func nodes(_ element: AXUIElement, _ depth: Int = 0) -> [AXUIElement] {
    if depth > 16 { return [] }
    var result = [element]
    for child in children(element) {
        result += nodes(child, depth+1)
        if result.count > 2000 { break }
    }
    return result
}
func texts(_ element: AXUIElement) -> [String] {
    [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute, "AXIdentifier"].map { str(element, $0) }
}
func appWindows(_ appRoot: AXUIElement) -> [AXUIElement] {
    (attr(appRoot,kAXWindowsAttribute) as? [AXUIElement] ?? []).filter { window in
        // macOS screen-sharing badge is not an editor or a confirmation dialog.
        let contents = children(window)
        return !(str(window,kAXTitleAttribute) == "Window" && contents.count == 1 &&
                 str(contents[0],kAXTitleAttribute) == "WindowSharingSessionButton")
    }
}
func windowNodes(_ appRoot: AXUIElement) -> [AXUIElement] {
    appWindows(appRoot).flatMap { nodes($0) }
}
func home(_ items: [AXUIElement]) -> Bool {
    items.contains { texts($0).contains { $0.contains("HomePageStartProject") } }
}
func opened(_ items: [AXUIElement], _ name: String, _ pid: pid_t) -> Bool {
    guard !home(items), items.contains(where: { texts($0).contains("MainWindowTitleBarExportBtn") }) else { return false }
    // Qt does not expose the title-bar draft name. Confirm the editor view AND
    // exactly one open draft lock held by this main process, never just file existence.
    let process=Process(); let pipe=Pipe()
    process.executableURL=URL(fileURLWithPath:"/usr/sbin/lsof")
    process.arguments=["-nP","-a","-p",String(pid),"-Fn"]
    process.standardOutput=pipe; process.standardError=FileHandle.nullDevice
    do { try process.run() } catch { return false }
    let data=pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
    let prefix="n" + FileManager.default.homeDirectoryForCurrentUser.path + "/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/"
    let locks=Set((String(data:data,encoding:.utf8) ?? "").split(separator:"\n").map(String.init).filter { $0.hasPrefix(prefix) && $0.hasSuffix("/.locked") })
    return locks == Set([prefix + name + "/.locked"])
}
func clickRect(_ rectangle: CGRect) -> Bool {
    // Only click bounds returned by accessibility for this exact draft label.
    guard let target = mainApp(), NSWorkspace.shared.frontmostApplication?.processIdentifier == target.processIdentifier else { return false }
    guard rectangle.width >= 8, rectangle.width <= 650, rectangle.height >= 8,
          rectangle.height <= 240, rectangle.minX >= 0, rectangle.minY >= 0 else { return false }
    let point = CGPoint(x: rectangle.midX, y: rectangle.midY)
    for (kind, count) in [(CGEventType.leftMouseDown, 1), (.leftMouseUp, 1), (.leftMouseDown, 2), (.leftMouseUp, 2)] {
        guard let event = CGEvent(mouseEventSource: nil, mouseType: kind, mouseCursorPosition: point, mouseButton: .left) else { return false }
        event.setIntegerValueField(.mouseEventClickState, value: Int64(count))
        event.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.05)
    }
    return true
}
func pressDraft(_ items: [AXUIElement], _ name: String) -> Bool {
    let label = "HomePageDraftTitle:" + name
    let exact = items.filter { texts($0).contains { $0 == name || $0 == label } }
    if exact.count == 1 {
        let element = exact[0]
        if let titleRect = rectangle(element) {
            let cards = items.filter {
                str($0,kAXDescriptionAttribute) == "HomePageDraft" &&
                rectangle($0)?.contains(CGPoint(x:titleRect.midX,y:titleRect.midY)) == true
            }
            if cards.count == 1, let cardRect = rectangle(cards[0]) {
                // The title label itself ignores clicks in this Qt build; its enclosing
                // uniquely matching card contains the actual open target.
                return clickRect(cardRect)
            }
        }
        if str(element,kAXRoleAttribute) == kAXButtonRole,
           AXUIElementPerformAction(element, kAXPressAction as CFString) == .success { return true }
        if let p = attr(element, kAXPositionAttribute), let s = attr(element, kAXSizeAttribute),
           CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() {
            var point = CGPoint.zero; var size = CGSize.zero
            if AXValueGetValue(p as! AXValue, .cgPoint, &point), AXValueGetValue(s as! AXValue, .cgSize, &size) {
                return clickRect(CGRect(origin: point, size: size))
            }
        }
    }
    // Some Qt builds expose a whole grid as one text node. Do not guess coordinates.
    for element in items {
        let value = str(element, kAXValueAttribute) as NSString
        let range = value.range(of: label)
        if range.location == NSNotFound { continue }
        var cfRange = CFRange(location: range.location, length: range.length)
        guard let param = AXValueCreate(.cfRange, &cfRange) else { continue }
        var bounds: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(element, kAXBoundsForRangeParameterizedAttribute as CFString, param, &bounds) == .success,
              let bounds = bounds, CFGetTypeID(bounds) == AXValueGetTypeID() else { continue }
        var rect = CGRect.zero
        if AXValueGetValue(bounds as! AXValue, .cgRect, &rect), clickRect(rect) { return true }
    }
    return false
}
guard args.count == 2, args[0] == "--open" else {
    output(["draftOpened":false,"openState":"invalid-request"]); exit(0)
}
let path = URL(fileURLWithPath:args[1]).standardizedFileURL
let allowedRoot = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Movies/JianyingPro/User Data/Projects/com.lveditor.draft")
guard path.deletingLastPathComponent() == allowedRoot, path.lastPathComponent.hasPrefix("CJH_"),
      path.resolvingSymlinksInPath() == path, FileManager.default.fileExists(atPath:path.appendingPathComponent("draft_info.json").path) else {
    output(["draftOpened":false,"openState":"invalid-draft","note":"只打开工作台在默认目录生成的独立草稿。"]); exit(0)
}
let name = path.lastPathComponent
var currentApp = app
var currentRoot = root
var items = windowNodes(currentRoot)
for _ in 0..<32 {
    if items.contains(where:{ texts($0).contains { $0.hasPrefix("HomePageDraftTitle:") || $0 == "MainWindowTitleBarExportBtn" } }) { break }
    RunLoop.current.run(until:Date().addingTimeInterval(0.25)); items=windowNodes(currentRoot)
}
if opened(items, name, currentApp.processIdentifier) {
    output(["draftOpened":true,"openState":"draft-opened","editorVerified":true,"note":"本次草稿已在剪映编辑页打开。"]); exit(0)
}
guard home(items), appWindows(currentRoot).count == 1 else {
    output(["draftOpened":false,"openState":"editor-busy","needsUserAction":true,"note":"剪映不是单独首页，可能正在编辑其他项目或有弹窗；未切换项目，请保存后返回首页再试。"]); exit(0)
}
if !items.contains(where: { texts($0).contains { $0.contains(name) } }) {
    // Refresh disk-created drafts only from a verified idle home window. Never force quit.
    guard currentApp.terminate() else {
        output(["draftOpened":false,"openState":"refresh-required","note":"首页没有刷新且正常退出请求未接受，未强退剪映。"]); exit(0)
    }
    for _ in 0..<80 {
        if currentApp.isTerminated { break }
        RunLoop.current.run(until:Date().addingTimeInterval(0.25))
    }
    guard currentApp.isTerminated else {
        output(["draftOpened":false,"openState":"confirmation-required","needsUserAction":true,"note":"剪映等待退出确认；请先处理弹窗，不会自动确认或强退。"]); exit(0)
    }
    let launch = Process(); launch.executableURL = URL(fileURLWithPath:"/usr/bin/open")
    launch.arguments = ["-a", appURL.path]
    do { try launch.run(); launch.waitUntilExit() } catch {
        output(["draftOpened":false,"openState":"launch-failed"]); exit(0)
    }
    for _ in 0..<80 {
        if let relaunched = mainApp() {
            currentApp = relaunched; currentRoot = AXUIElementCreateApplication(relaunched.processIdentifier)
            AXUIElementSetMessagingTimeout(currentRoot, 2)
            items = windowNodes(currentRoot)
            if home(items), items.contains(where: { texts($0).contains { $0.hasPrefix("HomePageDraftTitle:") } }) { break }
        }
        RunLoop.current.run(until:Date().addingTimeInterval(0.25))
    }
}
guard home(items), appWindows(currentRoot).count == 1 else {
    output(["draftOpened":false,"openState":"homepage-required","note":"请处理剪映启动弹窗并返回首页后重试。"]); exit(0)
}
currentApp.activate(options:[])
RunLoop.current.run(until:Date().addingTimeInterval(0.3))
items = windowNodes(currentRoot)
guard home(items), appWindows(currentRoot).count == 1 else {
    output(["draftOpened":false,"openState":"editor-busy","note":"激活剪映后界面发生变化，停止自动点击。"]); exit(0)
}
guard pressDraft(items, name) else {
    output(["draftOpened":false,"openState":"controls-unsupported","needsUserAction":true,"note":"当前剪映未暴露可安全定位的目标草稿控件；没有按猜测坐标点击，请在首页打开本次草稿。"]); exit(0)
}
for _ in 0..<40 {
    if opened(windowNodes(currentRoot), name, currentApp.processIdentifier) {
        output(["draftOpened":true,"openState":"draft-opened","editorVerified":true,"note":"已核对草稿名称并进入剪映编辑页，可继续精剪。"]); exit(0)
    }
    RunLoop.current.run(until:Date().addingTimeInterval(0.25))
}
output(["draftOpened":false,"openState":"open-unverified","needsUserAction":true,"note":"已定位并请求打开本次草稿，但未取得编辑页确认；请检查剪映，未重复点击。"])
