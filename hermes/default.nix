# Native Hermes Agent and its restricted Gmail integration.
{ hermes-agent }:
{ pkgs, config, ... }:
{
  imports = [
    ./mail.nix
    hermes-agent.nixosModules.default
  ];

  # Let echo administer the service's shared workspace.
  users.users.echo.extraGroups = [ "hermes" ];

  services.hermes-agent = {
    enable = true;
    package = hermes-agent.packages.${pkgs.stdenv.hostPlatform.system}.messaging;
    addToSystemPackages = true;

    # Runtime secret files: string paths keep keys out of the Nix store.
    environmentFiles = [
      "/home/echo/.config/hermes/discord.env"
      "/home/echo/.config/hermes/openrouter.env"
    ];
    environment = {
      DISCORD_ALLOWED_USERS = "515629544980611113";
      DISCORD_ALLOW_ALL_USERS = "false";
      GATEWAY_ALLOW_ALL_USERS = "false";
      DISCORD_ALLOW_BOTS = "none";
    };
    settings.model = {
      provider = "openai-codex";
      default = "gpt-6.1-sol";
      # Clear any old endpoint; use the native Codex transport.
      base_url = null;
      api_mode = "codex_responses";
    };
    # Kept for manual OpenRouter use; does not apply to Codex.
    settings.provider_routing = {
      only = [ "azure" ];
      data_collection = "deny";
      require_parameters = true;
      sort = "price";
    };
    settings.fallback_providers = [ ];
    settings.discord = {
      require_mention = true;
      auto_thread = false;
      history_backfill = false;
    };

    # Existing runtime state stays in /var/lib/hermes; no migration.
    # Only AGENTS is managed by Nix. SOUL, memories, skills and OAuth
    # remain in runtime storage and are not part of this source folder.
    workingDirectory = "/var/lib/hermes/workspace";
    documents."AGENTS.md" = builtins.readFile ./AGENTS.md;
  };

  # Refresh the gateway's cached instructions only when their content changes.
  systemd.services.hermes-agent.restartTriggers = [
    (pkgs.writeText "hermes-workspace-agents.md"
      config.services.hermes-agent.documents."AGENTS.md")
  ];
}
