package org.cha0scollective.mvt;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import net.minecraft.client.Minecraft;
import net.minecraft.SharedConstants;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.DisconnectedScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.Difficulty;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.entity.MoverType;
import net.neoforged.fml.ModList;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.gametest.GameTestHooks;
import java.nio.file.Path;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** Private request/completion channel. Candidate mods are not trusted to validate themselves. */
final class Control {
    private static final Gson JSON = new Gson();
    private static Path directory;
    private static String runId;
    private static long sequence;
    private static long ticks;
    private static boolean pending;
    private static boolean configured;
    static void install() {
        if (System.getenv("MVT_CONTROL_DIR") == null) return;
        // Client-dev also enables GameTests through this vanilla IDE flag. This
        // opt-in lane uses ordinary gameplay semantics, with GameTests separate.
        if ("development".equals(System.getenv("MVT_CLIENT_MODE"))) SharedConstants.IS_RUNNING_IN_IDE = false;
        directory = Path.of(System.getenv("MVT_CONTROL_DIR"));
        runId = System.getenv("MVT_RUN_ID");
        if (runId == null || !runId.matches("[a-f0-9]{32}")) throw new IllegalStateException("Invalid MVT run identity");
        NeoForge.EVENT_BUS.addListener(Control::tick);
    }
    private static void tick(ClientTickEvent.Post event) {
        Minecraft mc = Minecraft.getInstance();
        ticks++;
        try {
            if (!configured) {
                mc.options.renderDistance().set(2);
                mc.options.simulationDistance().set(5);
                mc.options.framerateLimit().set(20);
                mc.options.enableVsync().set(false);
                mc.options.pauseOnLostFocus = false;
                configured = true;
            }
            if (ticks % 20 == 0) {
                JsonObject heartbeat = new JsonObject();
                heartbeat.addProperty("protocol", 1);
                heartbeat.addProperty("run_id", runId);
                heartbeat.addProperty("event", "heartbeat");
                heartbeat.addProperty("tick", ticks);
                Files.writeString(directory.resolve("events.jsonl"), JSON.toJson(heartbeat)+"\n", StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            }
            Path requestPath = directory.resolve("request.json");
            if (pending || !Files.exists(requestPath)) return;
            if (Files.size(requestPath) > 100_000) throw new IllegalStateException("Oversized MVT request");
            JsonObject request = JSON.fromJson(Files.readString(requestPath), JsonObject.class);
            if (request.get("protocol").getAsInt() != 1 || !runId.equals(request.get("run_id").getAsString()))
                throw new IllegalStateException("MVT protocol/run mismatch");
            long next = request.get("sequence").getAsLong();
            if (next <= sequence) return;
            if (next != sequence + 1) throw new IllegalStateException("MVT sequence gap");
            sequence = next;
            pending = true;
            try {
                JsonObject args = request.getAsJsonObject("arguments");
                String operation = request.get("operation").getAsString();
                if (operation.equals("server_command") || operation.equals("save_world")) {
                    var server = mc.getSingleplayerServer();
                    if (server == null) throw new IllegalStateException("Integrated server is unavailable");
                    boolean save = operation.equals("save_world");
                    String command = save ? "" : args.get("command").getAsString();
                    server.submit(() -> {
                        // save-all is registered only on dedicated servers. Use
                        // that command's own flush API for integrated worlds.
                        if (save) return server.saveEverything(true, true, true) ? 1 : 0;
                        try { return server.getCommands().getDispatcher().execute(command, server.createCommandSourceStack().withSuppressedOutput()); }
                        catch (Exception commandError) { throw new IllegalStateException("Integrated command failed", commandError); }
                    })
                        .whenComplete((success, error) -> mc.execute(() -> {
                            JsonObject result = new JsonObject();
                            if (error == null) result.addProperty("success", success);
                            complete(request, result, error == null ? null : commandFailure(error));
                        }));
                    return;
                }
                JsonObject result = action(mc, operation, args);
                complete(request, result, null);
            } catch (Exception error) {
                complete(request, new JsonObject(), error.getClass().getSimpleName()+": "+error.getMessage());
            }
        } catch (Exception fatal) {
            // Do not continue after protocol corruption, and never print request/launch data.
            mc.stop();
        }
    }

    private static String commandFailure(Throwable error) {
        while (error.getCause() != null) error = error.getCause();
        String detail = error.getClass().getSimpleName()+": "+error.getMessage();
        return "Integrated server command failed: "+detail.substring(0, Math.min(500, detail.length()));
    }

    private static void complete(JsonObject request, JsonObject result, String error) {
        try {
            JsonObject response = new JsonObject();
            for (String key : new String[]{"protocol", "run_id", "request_id", "sequence"}) response.add(key, request.get(key));
            response.addProperty("status", error == null ? "completed" : "failed");
            response.add("result", result);
            if (error != null) response.addProperty("detail", error);
            Path tmp = directory.resolve("response.tmp");
            Files.writeString(tmp, JSON.toJson(response));
            Files.move(tmp, directory.resolve("response.json"), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (Exception fatal) {
            Minecraft.getInstance().stop();
        } finally { pending = false; }
    }

    private static BlockPos position(JsonObject args) {
        return new BlockPos(args.get("x").getAsInt(), args.get("y").getAsInt(), args.get("z").getAsInt());
    }

    private static JsonObject action(Minecraft mc, String operation, JsonObject args) {
        JsonObject result = new JsonObject();
        switch (operation) {
            case "snapshot": return snapshot(mc, args);
            case "create_world": {
                String name = args.get("name").getAsString();
                if (!name.matches("mvt-[a-f0-9]{32}")) throw new IllegalArgumentException("Invalid test world name");
                if (Files.exists(mc.gameDirectory.toPath().resolve("saves").resolve(name))) throw new IllegalStateException("Test world already exists");
                var settings = new LevelSettings(name, GameType.CREATIVE, false, Difficulty.PEACEFUL, true, new GameRules(), WorldDataConfiguration.DEFAULT);
                mc.createWorldOpenFlows().createFreshLevel(name, settings, new WorldOptions(8675309L, true, false),
                    registries -> registries.registryOrThrow(Registries.WORLD_PRESET).getHolderOrThrow(WorldPresets.NORMAL).value().createWorldDimensions(), new TitleScreen());
                break;
            }
            case "load_world":
                String saved = args.get("name").getAsString();
                if (!saved.matches("mvt-[a-f0-9]{32}")) throw new IllegalArgumentException("Invalid test world name");
                mc.createWorldOpenFlows().openWorld(saved, () -> mc.setScreen(new TitleScreen()));
                break;
            case "join": {
                String address = args.get("address").getAsString();
                ConnectScreen.startConnecting(new TitleScreen(), mc, ServerAddress.parseString(address),
                    new ServerData("MVT", address, ServerData.Type.OTHER), false, null);
                break;
            }
            case "disconnect":
                if (mc.level != null) mc.level.disconnect();
                mc.disconnect(new TitleScreen());
                break;
            case "quit":
                if (mc.level != null) mc.level.disconnect();
                mc.disconnect(new TitleScreen());
                mc.stop();
                break;
            case "move":
                requireWorld(mc);
                mc.player.move(MoverType.SELF, new Vec3(args.get("dx").getAsDouble(), 0, args.get("dz").getAsDouble()));
                break;
            case "place": {
                requireWorld(mc);
                String id = args.get("item").getAsString();
                int slot = -1;
                for (int i=0; i<9; i++) {
                    if (BuiltInRegistries.ITEM.getKey(mc.player.getInventory().getItem(i).getItem()).toString().equals(id)) { slot=i; break; }
                }
                if (slot < 0) throw new IllegalStateException("Required item is not in the hotbar");
                mc.player.getInventory().selected = slot;
                BlockPos support = position(args).below();
                mc.gameMode.useItemOn(mc.player, InteractionHand.MAIN_HAND,
                    new BlockHitResult(Vec3.atCenterOf(support).add(0,0.5,0), Direction.UP, support, false));
                break;
            }
            case "open_container": {
                requireWorld(mc);
                BlockPos pos = position(args);
                mc.gameMode.useItemOn(mc.player, InteractionHand.MAIN_HAND,
                    new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false));
                break;
            }
            case "transfer":
                requireWorld(mc);
                if (mc.player.containerMenu == mc.player.inventoryMenu) throw new IllegalStateException("No container is open");
                mc.gameMode.handleInventoryMouseClick(mc.player.containerMenu.containerId, args.get("slot").getAsInt(), 0, ClickType.QUICK_MOVE, mc.player);
                break;
            case "close_container":
                requireWorld(mc);
                mc.player.closeContainer();
                break;
            default: throw new IllegalArgumentException("Unknown MVT operation");
        }
        return result;
    }

    private static void requireWorld(Minecraft mc) {
        if (mc.player == null || mc.level == null || mc.gameMode == null) throw new IllegalStateException("Client world is unavailable");
    }

    private static JsonObject item(ItemStack stack) {
        JsonObject item = new JsonObject();
        item.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
        item.addProperty("count", stack.getCount());
        return item;
    }

    private static JsonObject snapshot(Minecraft mc, JsonObject args) {
        JsonObject state = new JsonObject();
        state.addProperty("tick", ticks);
        state.addProperty("gametest_enabled", GameTestHooks.isGametestEnabled());
        state.addProperty("render_distance", mc.options.renderDistance().get());
        state.addProperty("simulation_distance", mc.options.simulationDistance().get());
        state.addProperty("max_fps", mc.options.framerateLimit().get());
        state.addProperty("title_ready", mc.screen instanceof TitleScreen && mc.getOverlay() == null);
        state.addProperty("world_ready", mc.player != null && mc.level != null && mc.getOverlay() == null);
        state.addProperty("screen", mc.screen == null ? "none" : mc.screen.getClass().getSimpleName());
        if (mc.screen instanceof DisconnectedScreen)
            state.addProperty("disconnect_reason", mc.screen.getNarrationMessage().getString());
        JsonObject mods = new JsonObject();
        ModList.get().getMods().forEach(mod -> mods.addProperty(mod.getModId(), mod.getVersion().toString()));
        state.add("mods", mods);
        if (mc.player != null && mc.level != null) {
            state.addProperty("uuid", mc.player.getUUID().toString());
            state.addProperty("dimension", mc.level.dimension().location().toString());
            state.addProperty("x", mc.player.getX());
            state.addProperty("z", mc.player.getZ());
            JsonArray inventory = new JsonArray();
            for (int i=0; i<mc.player.getInventory().getContainerSize(); i++) inventory.add(item(mc.player.getInventory().getItem(i)));
            state.add("inventory", inventory);
            JsonArray menu = new JsonArray();
            if (mc.player.containerMenu != mc.player.inventoryMenu) mc.player.containerMenu.slots.forEach(slot -> menu.add(item(slot.getItem())));
            state.add("menu", menu);
            if (args.has("x")) {
                BlockPos pos = position(args);
                state.addProperty("chunk_loaded", mc.level.hasChunkAt(pos));
                if (mc.level.hasChunkAt(pos)) state.addProperty("block", BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(pos).getBlock()).toString());
            }
        }
        return state;
    }
}
