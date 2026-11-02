// Server-side KubeJS 6 (Forge 1.19.2). Only a bounded whitelist-add operation.
// This does not grant OP or permission to arbitrary commands.
ServerEvents.commandRegistry(event => {
  const Word = Java.loadClass('com.mojang.brigadier.arguments.StringArgumentType');
  event.register(event.commands.literal('wladd')
    .requires(source => source.hasPermission(0))
    .then(event.commands.argument('nick', Word.word())
      .executes(ctx => {
        const nick = String(Word.getString(ctx, 'nick'));
        if (nick.length < 3 || nick.length > 16 || /[^A-Za-z0-9_]/.test(nick)) {
          ctx.source.sendFailure(Text.red('Nick Java musi miec 3-16 znakow: litery, cyfry lub _.'));
          return 0;
        }
        const result = ctx.source.server.runCommandSilent('whitelist add ' + nick);
        if (result > 0) {
          ctx.source.sendSuccess(Text.green('Dodano ' + nick + ' do whitelisty.'), false);
        } else {
          ctx.source.sendFailure(Text.red('Nie dodano: konto nie istnieje, jest juz na liscie lub usluga Mojang nie odpowiada.'));
        }
        return result;
      })));
});

// Read-only permission/parse checks on the real dispatcher, without adding players.
ServerEvents.loaded(event => {
  const dispatcher = event.server.commands.dispatcher;
  const source = event.server.createCommandSourceStack().withPermission(0);
  const root = dispatcher.root;
  if (!root.getChild('wladd') || !root.getChild('wladd').canUse(source)) {
    throw new Error('WLADD self-test: command unavailable at permission level 0');
  }
  ['op', 'whitelist', 'stop', 'gamemode', 'function'].forEach(name => {
    if (root.getChild(name) && root.getChild(name).canUse(source)) {
      throw new Error('WLADD self-test: unexpected public administrative command: ' + name);
    }
  });
  const valid = dispatcher.parse('wladd BonLeee', source);
  if (valid.reader.canRead() || valid.context.build('wladd BonLeee').command == null) {
    throw new Error('WLADD self-test: valid nickname did not parse');
  }
  ['wladd BonLeee extra', 'wladd BonLeee;op', 'wladd @a', 'wladd "BonLeee"'].forEach(input => {
    const parsed = dispatcher.parse(input, source);
    if (!parsed.reader.canRead() && parsed.context.build(input).command != null) {
      throw new Error('WLADD self-test: unexpected argument acceptance: ' + input);
    }
  });
  console.info('WLADD_SELFTEST_PASS permission=0; native admin commands denied; single-word parser verified; no list mutations.');
});
