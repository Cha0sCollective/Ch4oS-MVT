package org.cha0scollective.mvt;

import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.common.Mod;

/** This mod has no server payload or network registration. */
@Mod(value = "mvt_client", dist = Dist.CLIENT)
public final class ClientBridge {
    public ClientBridge() {
        // Control is installed only when the private per-run environment is present.
        Control.install();
    }
}
