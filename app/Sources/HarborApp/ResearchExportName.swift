import Foundation

/// The file a research report is saved as. A report never replaces a file that is
/// already there: an earlier export, with whatever the person changed in it, or
/// another research with the same title.
enum ResearchExportName {
    /// `<base>.md` in `dir`, else `<base> 2.md`, `<base> 3.md` and so on.
    static func unused(_ base: String, in dir: URL) -> URL {
        for number in 1...999 {
            let name = number == 1 ? "\(base).md" : "\(base) \(number).md"
            let candidate = dir.appendingPathComponent(name)
            if !exists(candidate) { return candidate }
        }
        return dir.appendingPathComponent("\(base) \(UUID().uuidString).md")
    }

    /// Anything at that path counts, a link that points nowhere included.
    private static func exists(_ url: URL) -> Bool {
        (try? FileManager.default.attributesOfItem(atPath: url.path)) != nil
    }
}
