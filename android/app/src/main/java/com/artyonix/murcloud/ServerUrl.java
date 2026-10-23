package com.artyonix.murcloud;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** Validation of the MURCLOUD server address entered on the connection screen. */
public final class ServerUrl {
    private ServerUrl() {}

    public static final class Invalid extends Exception {
        public final boolean needsHttps;

        Invalid(String msg, boolean needsHttps) {
            super(msg);
            this.needsHttps = needsHttps;
        }
    }

    /** Hosts that may use plain HTTP in debug builds (emulator host, this device). */
    static boolean isLocalDevHost(String host) {
        return "10.0.2.2".equals(host) || "localhost".equals(host) || "127.0.0.1".equals(host);
    }

    /**
     * Normalises input such as {@code murcloud.example.com} to {@code https://murcloud.example.com}.
     * Rejects anything that is not http(s), has user info, a path with query, or uses HTTP outside
     * the debug allow-list.
     */
    public static String normalize(String input, boolean allowLocalHttp) throws Invalid {
        String raw = input == null ? "" : input.trim();
        if (raw.isEmpty()) throw new Invalid("empty", false);
        if (!raw.contains("://")) raw = "https://" + raw;
        URI uri;
        try {
            uri = new URI(raw);
        } catch (URISyntaxException e) {
            throw new Invalid("syntax", false);
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri.getHost();
        if (host == null || host.isEmpty() || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null) {
            throw new Invalid("host", false);
        }
        if ("http".equals(scheme)) {
            if (!(allowLocalHttp && isLocalDevHost(host))) throw new Invalid("https required", true);
        } else if (!"https".equals(scheme)) {
            throw new Invalid("scheme", false);
        }
        String path = uri.getPath() == null ? "" : uri.getPath();
        while (path.endsWith("/")) path = path.substring(0, path.length() - 1);
        int port = uri.getPort();
        return scheme + "://" + host.toLowerCase(Locale.ROOT) + (port > 0 ? ":" + port : "") + path;
    }

    /** True when {@code url} belongs to the configured MURCLOUD origin. */
    public static boolean sameOrigin(String base, String url) {
        try {
            URI a = new URI(base);
            URI b = new URI(url);
            return a.getScheme().equalsIgnoreCase(b.getScheme()) && a.getHost().equalsIgnoreCase(b.getHost())
                    && effectivePort(a) == effectivePort(b);
        } catch (Exception e) {
            return false;
        }
    }

    private static int effectivePort(URI u) {
        if (u.getPort() > 0) return u.getPort();
        return "https".equalsIgnoreCase(u.getScheme()) ? 443 : 80;
    }
}
