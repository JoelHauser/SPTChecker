// ModReader: reads a scan request as JSON on stdin and writes extracted mod
// metadata as JSON to stdout. Exists so SPTChecker (Python) can get real
// CLR reflection over installed mod DLLs instead of hand-parsing ECMA-335
// bytes and guessing at bytecode shapes.
//
// Both readers load the target DLL into a private, collectible
// AssemblyLoadContext (not MetadataLoadContext -- that needs the runtime's
// own BCL assemblies as loose files on disk to resolve its "core assembly",
// which don't exist as separate files once this app is published
// self-contained + single-file; a real ALC resolves them through the
// process's own already-running runtime instead, so it works either way).
//
// Client (BepInEx) plugins: only Type objects and CustomAttributeData are
// read -- .NET doesn't run a type's static constructor or any other code
// just from loading an assembly and enumerating that, so the mod's actual
// code is never executed for this path, same safety property
// MetadataLoadContext would have given.
//
// Server (SPT v4) mods can't be read that way -- their metadata only exists
// as real property values on a constructed subclass of AbstractModMetadata,
// so that path does actually instantiate the type and execute whatever code
// that involves. That's an intentional, accepted trade-off: these are mods
// the user already runs on their own server.

using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using System.Text.Json.Serialization;

var request = JsonSerializer.Deserialize<ScanRequest>(Console.In.ReadToEnd(), JsonOpts.Read)
    ?? new ScanRequest();

var dllIndex = DllIndex.Build(request.SptRoot, request.ClientDlls, request.ServerDlls);

var response = new ScanResponse
{
    Client = request.ClientDlls.ToDictionary(p => p, p => ModReader.ReadClient(p, dllIndex)),
    Server = request.ServerDlls.ToDictionary(p => p, p => ModReader.ReadServer(p, dllIndex)),
};

Console.Out.Write(JsonSerializer.Serialize(response, JsonOpts.Write));

// ── Request / response shapes ────────────────────────────────────────────

class ScanRequest
{
    public string SptRoot { get; set; } = "";
    public List<string> ClientDlls { get; set; } = new();
    public List<string> ServerDlls { get; set; } = new();
}

class ModRecord
{
    public string? Guid { get; set; }
    public string? Name { get; set; }
    public string? Author { get; set; }
    public string? Version { get; set; }
    public string? SptVersion { get; set; }
    public string? Error { get; set; }
}

class ScanResponse
{
    public Dictionary<string, ModRecord?> Client { get; set; } = new();
    public Dictionary<string, ModRecord?> Server { get; set; } = new();
}

static class JsonOpts
{
    public static readonly JsonSerializerOptions Read = new(JsonSerializerDefaults.Web);
    public static readonly JsonSerializerOptions Write = new(JsonSerializerDefaults.Web)
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
}

// ── Shared: index every DLL under the SPT root + the target DLLs' own
// folders, so the load context can resolve whatever framework assemblies a
// mod references, regardless of exactly where SPT keeps them. ──────────

static class DllIndex
{
    public static Dictionary<string, string> Build(string sptRoot, List<string> clientDlls, List<string> serverDlls)
    {
        var index = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        void AddDir(string dir)
        {
            if (!Directory.Exists(dir)) return;
            foreach (var path in Directory.EnumerateFiles(dir, "*.dll", SearchOption.AllDirectories))
            {
                var name = Path.GetFileNameWithoutExtension(path);
                index.TryAdd(name, path); // first one found wins; duplicates are rare and any match is fine
            }
        }

        if (!string.IsNullOrEmpty(sptRoot)) AddDir(sptRoot);
        foreach (var dir in clientDlls.Concat(serverDlls).Select(Path.GetDirectoryName).Distinct())
        {
            if (dir != null) AddDir(dir);
        }
        return index;
    }
}

static class ModReader
{
    public static ModRecord? ReadClient(string dllPath, Dictionary<string, string> dllIndex) =>
        InLoadContext(dllPath, dllIndex, assembly =>
        {
            var matches = new List<ModRecord>();
            foreach (var type in SafeGetTypes(assembly))
            {
                foreach (var attr in CustomAttributeData.GetCustomAttributes(type))
                {
                    if (attr.AttributeType.Name != "BepInPlugin") continue;
                    var args = attr.ConstructorArguments;
                    if (args.Count < 3) continue;
                    matches.Add(new ModRecord
                    {
                        Guid = args[0].Value?.ToString(),
                        Name = args[1].Value?.ToString(),
                        Version = args[2].Value?.ToString(),
                    });
                }
            }
            if (matches.Count != 1 || string.IsNullOrEmpty(matches[0].Guid))
                return null;
            return matches[0];
        });

    public static ModRecord? ReadServer(string dllPath, Dictionary<string, string> dllIndex) =>
        InLoadContext(dllPath, dllIndex, assembly =>
        {
            var metadataType = SafeGetTypes(assembly)
                .FirstOrDefault(t => !t.IsAbstract && t.BaseType?.Name == "AbstractModMetadata");
            if (metadataType == null)
                return null;

            var instance = Activator.CreateInstance(metadataType);
            string? Prop(string name) => metadataType.GetProperty(name)?.GetValue(instance)?.ToString();

            var guid = Prop("ModGuid");
            if (string.IsNullOrEmpty(guid))
                return null;

            return new ModRecord
            {
                Guid = guid,
                Name = Prop("Name"),
                Author = Prop("Author"),
                Version = Prop("Version"),
                SptVersion = Prop("SptVersion"),
            };
        });

    static ModRecord? InLoadContext(string dllPath, Dictionary<string, string> dllIndex,
                                    Func<Assembly, ModRecord?> read)
    {
        var alc = new AssemblyLoadContext("modreader-" + Guid.NewGuid(), isCollectible: true);
        try
        {
            alc.Resolving += (context, name) =>
                name.Name != null && dllIndex.TryGetValue(name.Name, out var path)
                    ? context.LoadFromAssemblyPath(path)
                    : null;

            using var stream = File.OpenRead(dllPath);
            var assembly = alc.LoadFromStream(stream);
            return read(assembly);
        }
        catch (Exception ex)
        {
            return new ModRecord { Error = ex.GetType().Name + ": " + ex.Message };
        }
        finally
        {
            alc.Unload();
        }
    }

    static IEnumerable<Type> SafeGetTypes(Assembly assembly)
    {
        try { return assembly.GetTypes(); }
        catch (ReflectionTypeLoadException ex) { return ex.Types.Where(t => t != null)!; }
    }
}
