package com.artyonix.murcloud;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class ServerUrlTest {
    @Test
    public void addsHttpsAndStripsSlash() throws Exception {
        assertEquals("https://murcloud.example.com", ServerUrl.normalize(" murcloud.example.com/ ", false));
        assertEquals("https://a.b:8443/mur", ServerUrl.normalize("https://A.B:8443/mur/", false));
    }

    @Test
    public void rejectsPlainHttpAndOddUrls() {
        ServerUrl.Invalid e = assertThrows(ServerUrl.Invalid.class, () -> ServerUrl.normalize("http://example.com", true));
        assertTrue(e.needsHttps);
        assertThrows(ServerUrl.Invalid.class, () -> ServerUrl.normalize("ftp://x", false));
        assertThrows(ServerUrl.Invalid.class, () -> ServerUrl.normalize("https://user:pw@x.com", false));
        assertThrows(ServerUrl.Invalid.class, () -> ServerUrl.normalize("", false));
        assertThrows(ServerUrl.Invalid.class, () -> ServerUrl.normalize("http://10.0.2.2:8000", false));
    }

    @Test
    public void allowsLocalHttpOnlyWhenEnabled() throws Exception {
        assertEquals("http://10.0.2.2:8000", ServerUrl.normalize("http://10.0.2.2:8000", true));
    }

    @Test
    public void sameOrigin() {
        assertTrue(ServerUrl.sameOrigin("https://m.example.com", "https://m.example.com/api/drive/download?path=/a"));
        assertTrue(ServerUrl.sameOrigin("https://m.example.com", "https://m.example.com:443/"));
        assertFalse(ServerUrl.sameOrigin("https://m.example.com", "https://evil.com/"));
        assertFalse(ServerUrl.sameOrigin("https://m.example.com", "http://m.example.com/"));
    }
}
