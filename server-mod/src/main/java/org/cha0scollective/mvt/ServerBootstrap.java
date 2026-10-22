package org.cha0scollective.mvt;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import net.minecraft.SharedConstants;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.loading.FMLEnvironment;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.gametest.GameTestHooks;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.internal.versions.neoforge.NeoForgeVersion;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/** Runtime setup only. Fixtures and independent assertions remain outside the server. */
@Mod(value = "mvt_server_bootstrap", dist = Dist.DEDICATED_SERVER)
public final class ServerBootstrap {
    private final String runId;
    private final Path directory;
    public ServerBootstrap() {
        runId = System.getenv("MVT_SERVER_RUN_ID");
        if (!"development".equals(System.getenv("MVT_SERVER_MODE")) || runId == null || !runId.matches("[a-f0-9]{32}"))
            throw new IllegalStateException("MVT server bootstrap requires a private development run");
        if (FMLEnvironment.production) throw new IllegalStateException("MVT requires the real NeoForge development launch");
        directory = Path.of(System.getenv("MVT_RUNTIME_DIR"));
        // Keep FML's real development identity. Only the automatic GameTest IDE
        // trigger is disabled, exactly as in the development client gameplay lane.
        SharedConstants.IS_RUNNING_IN_IDE = false;
        NeoForge.EVENT_BUS.addListener(this::started);
    }
    private void started(ServerStartedEvent event) {
        try {
            JsonObject state = new JsonObject();
            state.addProperty("protocol", 1);
            state.addProperty("run_id", runId);
            state.addProperty("mode", "development");
            state.addProperty("minecraft", SharedConstants.getCurrentVersion().getName());
            state.addProperty("neoforge", NeoForgeVersion.getVersion());
            state.addProperty("fml_production", FMLEnvironment.production);
            state.addProperty("gametest_enabled", GameTestHooks.isGametestEnabled());
            Path tmp = directory.resolve("runtime.tmp");
            Files.writeString(tmp, new Gson().toJson(state));
            Files.move(tmp, directory.resolve("runtime.json"), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (Exception error) {
            throw new IllegalStateException("Could not publish MVT server runtime identity", error);
        }
    }
}
