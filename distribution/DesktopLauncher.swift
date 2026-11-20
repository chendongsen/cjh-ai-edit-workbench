import AppKit
import CryptoKit
import Foundation
import Darwin

// Resolve the installed app, not the Desktop shortcut or the build machine.
let app = Bundle.main.bundleURL.resolvingSymlinksInPath()
let installRoot = app.deletingLastPathComponent()
let bundledRoot = app.appendingPathComponent("Contents/Resources/workbench")
let bundled = FileManager.default.fileExists(atPath: bundledRoot.path)
let root = bundled ? bundledRoot : installRoot
let selfInstall = bundled && FileManager.default.fileExists(atPath:
    root.appendingPathComponent("self-install.json").path)
    && !FileManager.default.fileExists(atPath: installRoot.appendingPathComponent("安装来源.json").path)
let packageInstall = bundled && FileManager.default.fileExists(atPath:
    installRoot.appendingPathComponent(".cjh-install-package").path)
let installing = selfInstall || packageInstall
let configuredData = ProcessInfo.processInfo.environment["CJH_DATA_DIR"]
let dataRoot = (configuredData.flatMap { $0.isEmpty ? nil : $0 }.map {
    URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath, relativeTo: root)
} ?? installRoot.appendingPathComponent(".local")).standardizedFileURL.resolvingSymlinksInPath()
let dataInsideApp = dataRoot.path == app.path || dataRoot.path.hasPrefix(app.path + "/")
let command = root.appendingPathComponent("启动剪辑工作台.command")
let runtime = root.appendingPathComponent("runtime/bin/python3")
let backend = root.appendingPathComponent("backend/launch.py")
let installer = root.appendingPathComponent(selfInstall ? "self_installer.py" : "installer.py")
let ready = !dataInsideApp && FileManager.default.isExecutableFile(atPath: command.path)
    && FileManager.default.isExecutableFile(atPath: runtime.path)
    && FileManager.default.isReadableFile(atPath: backend.path)
    && (!installing || FileManager.default.isReadableFile(atPath: installer.path))
let launchLogURL = installing
    ? FileManager.default.temporaryDirectory.appendingPathComponent("CJH-install-" + UUID().uuidString)
        .appendingPathComponent("安装日志.txt")
    : dataRoot.appendingPathComponent("launcher.log")
if CommandLine.arguments.contains("--check") {
    let result: [String: Any] = ["root": root.path, "command": command.path,
        "codeRoot": root.path, "dataRoot": dataRoot.path, "installRoot": installRoot.path,
        "layout": bundled ? "bundle" : "flat", "ready": ready,
        "installMode": selfInstall ? "self-contained" : (packageInstall ? "package" : "none"),
        "mode": installing ? "installer" : "background", "log": launchLogURL.path]
    print(String(data: try JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
    exit(ready ? 0 : 1)
}

final class Launcher: NSObject, NSApplicationDelegate {
    private var task: Process?
    private var output: FileHandle?
    private var statusItem: NSStatusItem?
    private var installWindow: NSWindow?
    private var stopping = false
    private var quitting = false
    private var checking = false
    private let logURL = launchLogURL
    private let port = ProcessInfo.processInfo.environment["CJH_PORT"] ?? "8765"
    private let identity = SHA256.hash(data: Data(root.path.utf8)).map { String(format: "%02x", $0) }.joined().prefix(16)

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard ready else {
            fail(dataInsideApp ? "数据目录不能位于 App 内，请使用安装目录旁的 .local。"
                : "安装文件不完整，请保留完整安装目录，不要单独移动 App。")
            return
        }
        if installing {
            installWorkbench()
            return
        }
        do {
            try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: logURL.path) {
                guard FileManager.default.createFile(atPath: logURL.path, contents: nil,
                    attributes: [.posixPermissions: 0o600]) else { throw CocoaError(.fileWriteUnknown) }
            }
            output = try FileHandle(forWritingTo: logURL)
            try output?.seekToEnd()
            try output?.write(contentsOf: Data("\n[\(ISO8601DateFormatter().string(from: Date()))] 桌面后台启动\n".utf8))
            let child = Process()
            child.executableURL = command
            child.currentDirectoryURL = root
            var environment = ProcessInfo.processInfo.environment
            environment["CJH_BACKGROUND"] = "1"
            environment["CJH_DATA_DIR"] = dataRoot.path
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            child.environment = environment
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = output
            child.standardError = output
            child.terminationHandler = { [weak self] process in
                let code = process.terminationStatus
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    try? self.output?.close()
                    if code != 0 && !self.stopping {
                        self.fail("后台服务未能继续运行。可查看本机启动日志：\n\(self.logURL.path)")
                    } else {
                        self.quitting = true
                        NSApplication.shared.terminate(nil)
                    }
                }
            }
            task = child
            try child.run()
            installMenu()
        } catch {
            fail("无法启动后台服务：\(error.localizedDescription)\n日志：\(logURL.path)")
        }
    }

    private func installWorkbench() {
        do {
            try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            guard FileManager.default.createFile(atPath: logURL.path, contents: nil,
                attributes: [.posixPermissions: 0o600]) else { throw CocoaError(.fileWriteUnknown) }
            output = try FileHandle(forWritingTo: logURL)
            let child = Process()
            child.executableURL = runtime
            child.arguments = ["-I", "-B", installer.path] + (selfInstall
                ? ["--source-app", app.path]
                : ["--package-root", installRoot.deletingLastPathComponent().path])
            child.currentDirectoryURL = root
            var environment = ProcessInfo.processInfo.environment
            for name in ["CJH_DATA_DIR", "PYTHONPATH", "PYTHONHOME"] { environment.removeValue(forKey: name) }
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONNOUSERSITE"] = "1"
            child.environment = environment
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = output
            child.standardError = output
            child.terminationHandler = { [weak self] process in
                let code = process.terminationStatus
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    try? self.output?.close()
                    if code == 0 {
                        self.installWindow?.close()
                        self.quitting = true
                        NSApplication.shared.terminate(nil)
                    } else {
                        self.fail("安装未完成。请查看本次安装日志：\n\(self.logURL.path)")
                    }
                }
            }
            task = child
            try child.run()
            showInstallProgress()
        } catch {
            fail("无法开始安装：\(error.localizedDescription)\n日志：\(logURL.path)")
        }
    }

    private func showInstallProgress() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 190),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "正在安装 CJH 工作台"
        window.isReleasedWhenClosed = false
        guard let content = window.contentView else { return }

        let spinner = NSProgressIndicator(frame: NSRect(x: 28, y: 100, width: 30, height: 30))
        spinner.style = .spinning
        spinner.isIndeterminate = true
        spinner.startAnimation(nil)
        content.addSubview(spinner)

        let description = NSTextField(wrappingLabelWithString: "正在复制离线组件并检查依赖。\n首次安装可能需要几分钟，请稍候。")
        description.frame = NSRect(x: 78, y: 89, width: 410, height: 52)
        description.font = NSFont.systemFont(ofSize: 14)
        content.addSubview(description)

        let note = NSTextField(wrappingLabelWithString: "完成后会自动打开工作台；关闭此窗口不会取消安装。")
        note.frame = NSRect(x: 28, y: 58, width: 464, height: 22)
        note.font = NSFont.systemFont(ofSize: 12)
        note.textColor = .secondaryLabelColor
        content.addSubview(note)

        let log = NSButton(title: "查看安装日志", target: self, action: #selector(openLog))
        log.frame = NSRect(x: 364, y: 17, width: 130, height: 32)
        log.bezelStyle = .rounded
        content.addSubview(log)
        installWindow = window
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func installMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "CJH"
        item.button?.toolTip = "剪辑工作台正在后台运行"
        let menu = NSMenu()
        for (title, action) in [("打开工作台", #selector(openWorkbench)),
                                ("查看启动日志", #selector(openLog)),
                                ("退出后台服务…", #selector(stopWorkbench))] {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
            entry.target = self
            menu.addItem(entry)
        }
        item.menu = menu
        statusItem = item
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if installing { return false }
        openWorkbench()
        return false
    }

    @objc private func openWorkbench() {
        guard !checking, !stopping, let health = URL(string: "http://127.0.0.1:\(port)/api/health") else { return }
        checking = true
        var request = URLRequest(url: health)
        request.timeoutInterval = 3
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            let value = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.checking = false
                // First launch opens the page after readiness; re-clicking never starts a second worker.
                if value?["instance"] as? String == String(self.identity),
                   let page = URL(string: "http://127.0.0.1:\(self.port)/#engines") {
                    NSWorkspace.shared.open(page)
                }
            }
        }.resume()
    }

    @objc private func openLog() { NSWorkspace.shared.open(logURL) }

    private func hasActiveJobs() -> Bool {
        let jobs = dataRoot.appendingPathComponent("jobs")
        guard FileManager.default.fileExists(atPath: jobs.path) else { return false }
        guard let directories = try? FileManager.default.contentsOfDirectory(at: jobs, includingPropertiesForKeys: nil) else { return true }
        for directory in directories {
            let status = directory.appendingPathComponent("status.json")
            guard FileManager.default.fileExists(atPath: status.path) else { continue }
            guard let data = try? Data(contentsOf: status),
                  let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return true }
            if ["running", "queued"].contains(value["status"] as? String ?? "") { return true }
        }
        return false
    }

    @objc private func stopWorkbench() {
        guard !stopping, let child = task, child.isRunning else { return }
        NSApplication.shared.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        if hasActiveJobs() {
            alert.messageText = "粗剪任务尚未结束"
            alert.informativeText = "请先在工作台确认排队或运行中的任务已结束，再退出。没有中断任务。"
            alert.runModal()
            return
        }
        alert.messageText = "退出剪辑工作台后台服务？"
        alert.informativeText = "请确认其他网页没有正在提交或导入的素材。退出只停止这次启动的服务，不关闭达芬奇、剪映，也不删除素材或草稿。"
        alert.addButton(withTitle: "继续后台运行")
        alert.addButton(withTitle: "退出后台服务")
        guard alert.runModal() == .alertSecondButtonReturn, !hasActiveJobs() else { return }
        // .command execs Python; SIGINT reaches its KeyboardInterrupt/finally cleanup.
        stopping = true
        if kill(child.processIdentifier, SIGINT) != 0 {
            stopping = false
            alert.messageText = "未能停止后台服务"
            alert.informativeText = "服务可能已结束；没有终止其他进程，请查看启动日志。"
            alert.runModal()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting || task?.isRunning != true { return .terminateNow }
        if installing { return .terminateCancel }
        stopWorkbench()
        return .terminateCancel
    }

    private func fail(_ message: String) {
        installWindow?.close()
        NSApplication.shared.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = installing ? "工作台安装未完成" : "工作台启动未完成"
        alert.informativeText = message
        alert.addButton(withTitle: "知道了")
        if FileManager.default.fileExists(atPath: logURL.path) { alert.addButton(withTitle: "查看日志") }
        if alert.runModal() == .alertSecondButtonReturn { openLog() }
        quitting = true
        NSApplication.shared.terminate(nil)
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let delegate = Launcher()
application.delegate = delegate
application.run()
