# Only whitelist additions are available as an extra non-OP management action.
# Do not grant access to vanilla /function, /whitelist, /op or generic execution.
{ pkgs, ... }:
{
  services.minecraft-servers.servers.fabric.symlinks = {
    "mods/commandaliases-fabric-1.1.0+mc1.21.1.jar" = pkgs.fetchurl {
      url = "https://cdn.modrinth.com/data/dHTBnV1v/versions/UzTaUuxy/commandaliases-fabric-1.1.0%2Bmc1.21.1.jar";
      sha512 = "bfd326a14608c0af0a6baf68602fe2ce41224c29f6ead6f810c930d0fe35f06e3fb71485c92b5848d153b8dfb05b30a52b80e4fffd6b50d812df7b6eb5322bd5";
    };
    "config/commandaliases/wladd.json".value = {
      schemaVersion = 1;
      commandMode = "COMMAND_CUSTOM";
      command = "wladd";
      permission = 0;
      children = [ {
        child = "nick";
        type = "argument";
        # One unquoted word, NOT a greedy string: no spaces, selectors, quotes,
        # command separators or expression syntax may reach the server action.
        argumentType = "minecraft:word";
        permission = 0;
        actions = [ {
          command = "whitelist add {{nick}}";
          commandType = "SERVER";
        } ];
        # The alias library cannot report the result of a queued vanilla command.
        # Do not claim success when a profile lookup could still fail.
        message = "Zlecono dodanie {{nick}} do whitelisty. Nick musi istniec w Minecraft Java.";
      } ];
    };
  };
}
