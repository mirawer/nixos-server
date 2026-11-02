{
  description = "Home server with Cisco Ultimate V8Cr Minecraft and a paused Hermes Agent";

  inputs = {
    # Keep the server's current Nixpkgs revision.
    nixpkgs.url = "github:NixOS/nixpkgs/c3eea5b2156db11c7eeeada3dc737711255b253e";
    nix-minecraft = {
      url = "github:Infinidoge/nix-minecraft/0583a7a828d7964323edcce9c60b0d7a4b138b2c";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Hermes v0.21.5 / v2026.9.24, already tested on this server.
    hermes-agent.url = "github:NousResearch/hermes-agent/f97608f178d1ffeca59860195ab7da295f7c8e5f";
  };

  outputs = { nixpkgs, hermes-agent, nix-minecraft, ... }: {
    nixosConfigurations.server = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ./configuration.nix
        ./syncthing.nix
        nix-minecraft.nixosModules.minecraft-servers
        ./minecraft-cisco.nix
        (import ./hermes { inherit hermes-agent; })
        ./hermes-paused.nix
        ({ ... }: {
          nixpkgs.overlays = [ nix-minecraft.overlay ];
          nix.settings.experimental-features = [ "nix-command" "flakes" ];
        })
      ];
    };
  };
}
