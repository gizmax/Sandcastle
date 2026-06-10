import { useState } from "react";
import {
  DollarSign,
  Loader2,
  AlertCircle,
  LogOut,
  BadgeCheck,
  ArrowUpCircle,
  CheckCircle2,
  Copy,
  ExternalLink,
  Bell,
  Palette,
  Check,
  Moon,
  Sun,
  RotateCcw,
  Clock,
  Download,
} from "lucide-react";
import { api } from "@/api/client";
import { SectionCard, FieldLabel, HelperText } from "@/components/ui/SectionCard";
import { cn, inputClass } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";
import { useUpdateCheck } from "@/hooks/useUpdateCheck";
import { useNotifications } from "@/hooks/useNotifications";
import { useAccentColor, ACCENT_COLORS } from "@/hooks/useAccentColor";
import { useTheme } from "@/hooks/useTheme";
import { SaveButton } from "./SettingsShared";
import { useSettingsContext } from "./settingsContext";

/**
 * General tab — the everyday, friendly settings: appearance (theme + accent),
 * notifications, the per-run cost budget, software updates, license, and the
 * active session. Power-user knobs live in the Advanced tab instead.
 */
export default function GeneralPanel() {
  const { settings, updateField, isSectionDirty, savingSections, handleSave } = useSettingsContext();
  const { info: runtimeInfo } = useRuntimeInfo();
  const update = useUpdateCheck();
  const {
    permission: notifPermission,
    notificationsEnabled,
    requestPermission,
    toggleNotifications,
  } = useNotifications();
  const [copied, setCopied] = useState(false);
  const { accentColor, setAccentColor } = useAccentColor();
  const { theme, toggleTheme } = useTheme();

  // Update channel state
  const [updateChannel, setUpdateChannel] = useState<"stable" | "beta" | "pinned">("stable");
  const [pinnedVersion, setPinnedVersion] = useState("");

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Appearance */}
      <SectionCard
        icon={Palette}
        title="Appearance"
        description="Theme and accent color preferences"
      >
        <div className="space-y-5">
          {/* Theme toggle */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">Theme</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Switch between light and dark mode
              </p>
            </div>
            <button
              onClick={toggleTheme}
              className={cn(
                "flex items-center gap-2 rounded-lg border border-border px-3 py-1.5",
                "text-sm font-medium text-foreground hover:bg-border/40 transition-colors cursor-pointer",
              )}
            >
              {theme === "dark" ? (
                <><Moon className="h-4 w-4" /> Dark</>
              ) : (
                <><Sun className="h-4 w-4" /> Light</>
              )}
            </button>
          </div>

          {/* Accent color palette */}
          <div>
            <p className="text-sm font-medium text-foreground mb-3">Accent Color</p>
            <div className="flex flex-wrap gap-3">
              {ACCENT_COLORS.map((color) => {
                const isSelected = accentColor === color.id;
                return (
                  <button
                    key={color.id}
                    onClick={() => setAccentColor(color.id)}
                    className="group relative flex h-8 w-8 items-center justify-center rounded-full transition-transform duration-150 motion-reduce:transition-none hover:scale-110 cursor-pointer"
                    style={{
                      backgroundColor: theme === "dark" ? color.darkAccent : color.accent,
                      boxShadow: isSelected
                        ? `0 0 0 2px var(--color-background), 0 0 0 4px ${theme === "dark" ? color.darkAccent : color.accent}`
                        : undefined,
                    }}
                    title={color.label}
                    aria-label={`Set accent color to ${color.label}`}
                  >
                    {isSelected && (
                      <Check
                        className="h-4 w-4"
                        style={{
                          color: theme === "dark" ? color.darkAccentForeground : color.accentForeground,
                        }}
                        strokeWidth={3}
                      />
                    )}
                  </button>
                );
              })}
            </div>
            <div className="flex items-center gap-2 mt-2">
              <p className="text-xs text-muted-foreground">
                {ACCENT_COLORS.find((c) => c.id === accentColor)?.label ?? "Amber"}
                {accentColor === "amber" ? " (default)" : ""}
              </p>
              {accentColor !== "amber" && (
                <button
                  onClick={() => setAccentColor("amber")}
                  className="text-xs text-accent hover:underline cursor-pointer"
                >
                  Reset to default
                </button>
              )}
            </div>
          </div>
        </div>
      </SectionCard>

      {/* Notifications */}
      <SectionCard
        icon={Bell}
        title="Notifications"
        description="Get notified when workflows complete or fail"
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">
                Browser notifications for run completion
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Permission:{" "}
                <span
                  className={cn(
                    "font-medium",
                    notifPermission === "granted"
                      ? "text-success"
                      : notifPermission === "denied"
                        ? "text-error"
                        : "text-muted-foreground",
                  )}
                >
                  {notifPermission === "granted"
                    ? "Allowed"
                    : notifPermission === "denied"
                      ? "Blocked"
                      : "Not requested"}
                </span>
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={notificationsEnabled}
              onClick={toggleNotifications}
              disabled={notifPermission !== "granted"}
              className={cn(
                "relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors motion-reduce:transition-none cursor-pointer",
                notificationsEnabled && notifPermission === "granted"
                  ? "bg-accent"
                  : "bg-border",
                notifPermission !== "granted" && "opacity-60 cursor-not-allowed",
              )}
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform motion-reduce:transition-none",
                  notificationsEnabled && notifPermission === "granted"
                    ? "translate-x-5"
                    : "translate-x-0",
                )}
              />
            </button>
          </div>
          {notifPermission === "default" && (
            <button
              onClick={() => void requestPermission()}
              className={cn(
                "flex items-center gap-2 rounded-lg border border-accent/30 px-3 py-1.5",
                "text-sm font-medium text-accent",
                "hover:bg-accent/10 transition-colors cursor-pointer",
              )}
            >
              <Bell className="h-4 w-4" />
              Request Permission
            </button>
          )}
          {notifPermission === "denied" && (
            <p className="text-xs text-muted-foreground">
              Browser notifications are blocked. Update your browser settings to enable them.
            </p>
          )}
        </div>
      </SectionCard>

      {/* Budget & Costs */}
      <SectionCard
        icon={DollarSign}
        title="Budget & Costs"
        description="Default cost limits for workflow runs"
      >
        <div className="space-y-3">
          <div>
            <FieldLabel htmlFor="default_max_cost_usd">Default Max Cost per Run (USD)</FieldLabel>
            <input
              id="default_max_cost_usd"
              type="number"
              min={0}
              step={0.01}
              className={cn(inputClass, "max-w-xs")}
              value={settings.default_max_cost_usd}
              onChange={(e) => updateField("default_max_cost_usd", parseFloat(e.target.value) || 0)}
            />
            <HelperText>0 = unlimited</HelperText>
          </div>
          <div className="flex justify-end">
            <SaveButton
              dirty={isSectionDirty("budget")}
              saving={savingSections.has("budget")}
              onClick={() => handleSave("budget")}
            />
          </div>
        </div>
      </SectionCard>

      {/* Software Update */}
      {!update.loading && update.currentVersion && (
        <div id="software-update-section">
          <SectionCard
            icon={update.updateAvailable ? ArrowUpCircle : CheckCircle2}
            title="Software Update"
            description="Check for new Sandcastle versions"
          >
            {update.updateAvailable ? (
              <div className="space-y-4">
                {/* Version badges side by side */}
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-muted/50 border border-border text-muted-foreground">
                    Current: v{update.currentVersion}
                  </span>
                  <span className="text-muted-foreground">-&gt;</span>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-warning/15 border border-warning/30 text-warning">
                    <ArrowUpCircle className="h-3 w-3" />
                    v{update.latestVersion}
                  </span>
                </div>

                {/* Highlights */}
                {update.highlights.length > 0 && (
                  <ul className="space-y-1 pl-1">
                    {update.highlights.map((h, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-foreground">
                        <CheckCircle2 className="h-3.5 w-3.5 mt-0.5 text-accent shrink-0" />
                        {h}
                      </li>
                    ))}
                  </ul>
                )}

                {/* Update action area */}
                {update.updateStatus === "idle" && (
                  <div className="flex items-center gap-3 flex-wrap">
                    <button
                      onClick={() => void update.triggerUpdate()}
                      className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 transition-colors cursor-pointer"
                    >
                      <Download className="h-4 w-4" />
                      Update Now
                    </button>
                    {update.changelogUrl && (
                      <a
                        href={update.changelogUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        What's new
                      </a>
                    )}
                  </div>
                )}

                {/* Updating progress */}
                {update.updateStatus === "updating" && (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Updating to v{update.latestVersion}...
                  </div>
                )}

                {/* Success state */}
                {update.updateStatus === "success" && (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 text-sm text-success">
                      <CheckCircle2 className="h-4 w-4" />
                      Updated to v{update.latestVersion}! Restart to apply.
                    </div>
                    <button
                      onClick={() => void update.triggerRollback()}
                      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-border/40 transition-colors cursor-pointer"
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      Rollback
                    </button>
                  </div>
                )}

                {/* Error state */}
                {update.updateStatus === "error" && (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 text-sm text-destructive">
                      <AlertCircle className="h-4 w-4" />
                      Update failed: {update.updateError}
                    </div>
                    <button
                      onClick={() => void update.triggerUpdate()}
                      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-border/40 transition-colors cursor-pointer"
                    >
                      Retry
                    </button>
                  </div>
                )}

                {/* Manual install command */}
                <div className="flex items-center gap-2">
                  <code className="flex-1 overflow-x-auto rounded-lg bg-muted/50 border border-border px-3 py-2 text-xs sm:text-sm font-mono text-foreground">
                    {update.installCommand}
                  </code>
                  <button
                    onClick={() => {
                      void navigator.clipboard?.writeText(update.installCommand!).catch(() => {/* clipboard unavailable */});
                      setCopied(true);
                      setTimeout(() => setCopied(false), 2000);
                    }}
                    className="rounded-lg border border-border p-2 text-muted hover:text-foreground hover:bg-border/40 transition-colors cursor-pointer"
                    title="Copy command"
                  >
                    {copied ? <CheckCircle2 className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-success" />
                  <span className="text-sm text-foreground">
                    v{update.currentVersion} - You're on the latest version
                  </span>
                </div>
                {update.lastChecked && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    Last checked: {new Date(update.lastChecked).toLocaleString()}
                  </div>
                )}
              </div>
            )}

            {/* Update channel selector */}
            <div className="mt-4 pt-4 border-t border-border space-y-3">
              <FieldLabel htmlFor="update_channel">Update Channel</FieldLabel>
              <div className="flex items-center gap-3 flex-wrap">
                <select
                  value={updateChannel}
                  onChange={(e) => setUpdateChannel(e.target.value as "stable" | "beta" | "pinned")}
                  className={cn(inputClass, "w-40")}
                >
                  <option value="stable">Stable</option>
                  <option value="beta">Beta</option>
                  <option value="pinned">Pinned</option>
                </select>
                {updateChannel === "pinned" && (
                  <input
                    type="text"
                    value={pinnedVersion}
                    onChange={(e) => setPinnedVersion(e.target.value)}
                    placeholder="e.g. 0.27.0"
                    className={cn(inputClass, "w-36")}
                  />
                )}
              </div>
              <HelperText>
                {updateChannel === "stable" && "Receive only stable, tested releases."}
                {updateChannel === "beta" && "Receive pre-release versions with new features."}
                {updateChannel === "pinned" && "Lock to a specific version. No automatic updates."}
              </HelperText>
            </div>
          </SectionCard>
        </div>
      )}

      {/* License */}
      <SectionCard
        icon={BadgeCheck}
        title="License"
        description="Sandcastle license status and tier"
      >
        {runtimeInfo?.license ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-foreground">Status</span>
              <span
                className={cn(
                  "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium",
                  runtimeInfo.license.status === "valid"
                    ? "bg-success/15 border border-success/30 text-success"
                    : runtimeInfo.license.status === "expired"
                      ? "bg-warning/15 border border-warning/30 text-warning"
                      : runtimeInfo.license.status === "missing"
                        ? "bg-muted/15 border border-border text-muted-foreground"
                        : "bg-error/15 border border-error/30 text-error",
                )}
              >
                {runtimeInfo.license.status === "valid"
                  ? `${runtimeInfo.license.tier.charAt(0).toUpperCase() + runtimeInfo.license.tier.slice(1)} License`
                  : runtimeInfo.license.status === "missing"
                    ? "Community Mode"
                    : runtimeInfo.license.status.charAt(0).toUpperCase() + runtimeInfo.license.status.slice(1)}
              </span>
            </div>
            {runtimeInfo.license.status === "valid" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-3">
                <div>
                  <p className="text-sm font-medium text-foreground">Licensee</p>
                  <p className="text-sm text-muted-foreground">{runtimeInfo.license.licensee || "-"}</p>
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">Expires</p>
                  <p className="text-sm text-muted-foreground">{runtimeInfo.license.expires || "Never"}</p>
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">Max Seats</p>
                  <p className="text-sm text-muted-foreground">{runtimeInfo.license.max_seats || "Unlimited"}</p>
                </div>
              </div>
            )}
            {runtimeInfo.license.status === "missing" && (
              <p className="text-sm text-muted-foreground">
                All features are available. Set <code className="text-xs bg-muted px-1 rounded">LICENSE_KEY</code> in your .env for production use.{" "}
                <a
                  href="mailto:tom@pflanzer.cz"
                  className="text-accent hover:underline"
                >
                  Contact sales
                </a>
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Loading license info...</p>
        )}
      </SectionCard>

      {/* API Key / Session */}
      {api.hasStoredKey() && (
        <SectionCard
          icon={LogOut}
          title="Session"
          description="Connected via saved API key"
        >
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Key: <span className="font-mono">{api.storedKeyPrefix()}...</span>
            </p>
            <button
              onClick={() => {
                api.clearStoredKey();
                window.location.reload();
              }}
              className="flex items-center gap-2 rounded-lg border border-error/30 px-3 py-1.5 text-sm font-medium text-error hover:bg-error/10 transition-colors cursor-pointer"
            >
              <LogOut className="h-3.5 w-3.5" />
              Disconnect
            </button>
          </div>
        </SectionCard>
      )}
    </div>
  );
}
