# Server-only datapack for Minecraft Java 1.21 / 1.21.1 (data pack format 48).
# Uses native Disabilities 0.5.0 commands; does not unload the mod or run on load.
{ pkgs }:
let
  metadata = pkgs.writeText "pack.mcmeta" (builtins.toJSON {
    pack = {
      pack_format = 48;
      description = "Disabilities - operator on/off controls";
    };
  });
  on = pkgs.writeText "disability-on.mcfunction" ''
    # Enable grants every 10 minutes and restart the countdown.
    disability timer 10
  '';
  off = pkgs.writeText "disability-off.mcfunction" ''
    # Stop automatic grants before clearing everyone's saved disability state.
    disability timer off
    disability reset
  '';
in
pkgs.runCommand "minecraft-disability-control" { } ''
  mkdir -p "$out/data/disability/function"
  cp ${metadata} "$out/pack.mcmeta"
  cp ${on} "$out/data/disability/function/on.mcfunction"
  cp ${off} "$out/data/disability/function/off.mcfunction"
''
