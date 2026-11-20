// Local-only importer. No network, no shell execution, no edits to old drafts/indexes.
import Foundation
import CryptoKit

enum ImportError: Error { case invalid(String) }
func fail(_ message: String) throws -> Never { throw ImportError.invalid(message) }
let fm = FileManager.default
func hash(_ url: URL) throws -> String {
    let f = try FileHandle(forReadingFrom: url)
    defer { try? f.close() }
    var h = SHA256()
    while let data = try f.read(upToCount: 4 * 1024 * 1024), !data.isEmpty { h.update(data: data) }
    return h.finalize().map { String(format: "%02x", $0) }.joined()
}
func json(_ url: URL) throws -> [String: Any] {
    guard let d = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any] else {
        try fail("JSON 格式错误")
    }
    return d
}
func save(_ object: Any, _ url: URL) throws {
    try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
}
func relative(_ s: String) -> Bool {
    let p = s.split(separator: "/", omittingEmptySubsequences: false)
    return p.count == 2 && ["media", "audio"].contains(String(p[0])) &&
        !p.contains("..") && !p.contains("") && !s.contains("\\") && !s.contains("\0")
}
func rewrite(_ value: Any, _ dest: String, _ allowed: Set<String>) throws -> Any {
    if let d = value as? [String: Any] { return try d.mapValues { try rewrite($0, dest, allowed) } }
    if let a = value as? [Any] { return try a.map { try rewrite($0, dest, allowed) } }
    if let s = value as? String {
        let prefix = "__CJH_DRAFT_ROOT__/"
        if s.hasPrefix(prefix) {
            let tail = String(s.dropFirst(prefix.count))
            guard allowed.contains(tail) else { try fail("引用了未校验的素材") }
            return dest + "/" + tail
        }
        if s.hasPrefix("/Users/") || s.hasPrefix("/Volumes/") || s.hasPrefix("file://") {
            try fail("发现旧电脑路径，停止导入")
        }
    }
    return value
}
do {
    guard CommandLine.arguments.count == 2 || CommandLine.arguments.count == 4 else {
        try fail("用法：CJHImport 草稿包目录 [--test-root 测试目录]")
    }
    let bundle = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL.resolvingSymlinksInPath()
    let manifest = try json(bundle.appendingPathComponent("manifest.json"))
    guard manifest["version"] as? Int == 1,
          let files = manifest["files"] as? [String: [String: Any]], !files.isEmpty, files.count <= 2000 else {
        try fail("迁移清单无效")
    }
    let template = bundle.appendingPathComponent("draft-template.json")
    guard try hash(template) == manifest["contentSha256"] as? String else { try fail("工程校验不通过") }
    let metaTemplate = bundle.appendingPathComponent("meta-template.json")
    guard try hash(metaTemplate) == manifest["metaSha256"] as? String else { try fail("元信息校验不通过") }
    for (name, expected) in files {
        guard relative(name) else { try fail("非法素材路径") }
        let url = bundle.appendingPathComponent(name)
        guard url.standardizedFileURL.path == url.resolvingSymlinksInPath().path else { try fail("不接受素材符号链接") }
        let attr = try fm.attributesOfItem(atPath: url.path)
        guard (attr[.type] as? FileAttributeType) == .typeRegular,
              (attr[.size] as? NSNumber)?.int64Value == (expected["size"] as? NSNumber)?.int64Value,
              try hash(url) == expected["sha256"] as? String else { try fail("素材校验失败：" + name) }
    }
    let root: URL
    if CommandLine.arguments.count == 4 {
        guard CommandLine.arguments[2] == "--test-root" else { try fail("未知参数") }
        root = URL(fileURLWithPath: CommandLine.arguments[3]).standardizedFileURL.resolvingSymlinksInPath()
    } else {
        root = fm.homeDirectoryForCurrentUser.appendingPathComponent("Movies/JianyingPro/User Data/Projects/com.lveditor.draft")
    }
    var isDir: ObjCBool = false
    guard fm.fileExists(atPath: root.path, isDirectory: &isDir), isDir.boolValue else {
        try fail("未找到剪映默认草稿目录，请先安装并打开一次剪映。自定义目录暂不支持。")
    }
    let id = UUID().uuidString.uppercased()
    let name = "CJH_迁移_" + DateFormatter.localizedString(from: Date(), dateStyle: .short, timeStyle: .short)
        .replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-") + "_" + String(id.prefix(8))
    let dest = root.appendingPathComponent(name)
    let staging = root.appendingPathComponent(".cjh-import-" + id)
    guard !fm.fileExists(atPath: dest.path), !fm.fileExists(atPath: staging.path) else { try fail("目标已存在，未覆盖") }
    var content = try rewrite(try json(template), dest.path, Set(files.keys)) as! [String: Any]
    content["id"] = id; content["name"] = name
    try fm.createDirectory(at: staging, withIntermediateDirectories: false)
    // Failed imports remain hidden, with an explanatory file; never delete unrelated directories.
    do {
        for sub in ["media", "audio"] { try fm.createDirectory(at: staging.appendingPathComponent(sub), withIntermediateDirectories: false) }
        for file in files.keys {
            try fm.copyItem(at: bundle.appendingPathComponent(file), to: staging.appendingPathComponent(file))
        }
        try save(content, staging.appendingPathComponent("draft_content.json"))
        try save(content, staging.appendingPathComponent("draft_info.json"))
        let now = Int64(Date().timeIntervalSince1970 * 1e6)
        var meta = try json(metaTemplate)
        meta["draft_id"] = id; meta["draft_name"] = name
        meta["draft_fold_path"] = dest.path; meta["draft_root_path"] = root.path
        meta["tm_duration"] = content["duration"] ?? 0
        meta["tm_draft_create"] = now; meta["tm_draft_modified"] = now
        try save(meta, staging.appendingPathComponent("draft_meta_info.json"))
        try fm.moveItem(at: staging, to: dest)
    } catch {
        try? String(describing: error).write(to: staging.appendingPathComponent("导入失败.txt"), atomically: true, encoding: .utf8)
        throw error
    }
    try save(["ok": true, "draftPath": dest.path, "draftName": name, "mediaCount": files.count,
              "note": "文件校验与路径迁移完成；请在剪映实际打开并听审。"], bundle.appendingPathComponent("导入结果.json"))
    print("导入成功：\(dest.path)")
} catch {
    fputs("导入未完成：\(error)\n", stderr)
    exit(1)
}
