package com.voxdesk.sdk

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.DeserializationStrategy
import kotlinx.serialization.SerializationStrategy
import kotlinx.serialization.json.JsonElement

/** How the host app supplies credentials. The SDK never stores a token itself. */
fun interface TokenProvider {
    suspend fun accessToken(): String?
}

/** The server answered and refused. `code` is the machine-readable reason. */
class VoxDeskApiException(
    val status: Int,
    val code: String,
    override val message: String,
    val detail: JsonElement? = null,
) : Exception(message) {
    /** 4xx that a retry will not fix (429 excluded: it explicitly invites one). */
    val isPermanent: Boolean get() = status in 400..499 && status != 429
}

/** No answer arrived: offline, DNS failure, timeout. Retryable, and not a refusal. */
class VoxDeskNetworkException(message: String, cause: Throwable? = null) : Exception(message, cause)

/**
 * The HTTP client.
 *
 * `HttpURLConnection` rather than OkHttp: this SDK makes a dozen calls, and every
 * dependency ends up inside a customer's app bundle. The cost is that timeouts,
 * error decoding and idempotency have to be written out — so they are, once, here.
 *
 * Three behaviours are deliberate:
 *
 * * **A timeout always exists.** A request that never returns looks like a frozen
 *   screen and gets reported as "the app is broken".
 * * **A refusal is typed.** `VoxDeskApiException.code` is what callers branch on;
 *   `message` is for humans and may be localised server-side tomorrow.
 * * **Mutations carry `Idempotency-Key`.** The server deduplicates on it, so a
 *   retried device registration or time-off request happens once.
 */
class VoxDeskClient(
    baseUrl: String,
    private val tokenProvider: TokenProvider? = null,
    private val timeoutMillis: Int = 15_000,
    private val extraHeaders: Map<String, String> = emptyMap(),
    private val onUnauthorized: (() -> Unit)? = null,
) {
    /** Base URL with any trailing slash removed, so paths never double up. */
    val base: String = baseUrl.trimEnd('/')

    init {
        require(base.startsWith("http://") || base.startsWith("https://")) {
            "baseUrl must start with http:// or https://"
        }
    }

    fun url(path: String, query: Map<String, String> = emptyMap()): String {
        val normalized = if (path.startsWith("/")) path else "/$path"
        val rendered = query.entries
            .filter { it.value.isNotEmpty() }
            .joinToString("&") { "${encode(it.key)}=${encode(it.value)}" }
        return if (rendered.isEmpty()) base + normalized else base + normalized + "?" + rendered
    }

    suspend fun <T> get(
        path: String,
        deserializer: DeserializationStrategy<T>,
        query: Map<String, String> = emptyMap(),
    ): T = decode(send("GET", path, query, null, null), deserializer)

    suspend fun <T, B> post(
        path: String,
        body: B,
        serializer: SerializationStrategy<B>,
        deserializer: DeserializationStrategy<T>,
        idempotencyKey: String? = null,
    ): T = decode(send("POST", path, emptyMap(), Wire.json.encodeToString(serializer, body), idempotencyKey), deserializer)

    /** `POST` whose response body is not used. */
    suspend fun <B> postForget(
        path: String,
        body: B,
        serializer: SerializationStrategy<B>,
        idempotencyKey: String? = null,
    ) {
        send("POST", path, emptyMap(), Wire.json.encodeToString(serializer, body), idempotencyKey)
    }

    /** `POST` with no body at all, for action endpoints such as `/accept`. */
    suspend fun postEmpty(path: String, idempotencyKey: String? = null): String =
        send("POST", path, emptyMap(), null, idempotencyKey)

    suspend fun delete(path: String) {
        send("DELETE", path, emptyMap(), null, null)
    }

    private fun <T> decode(text: String, deserializer: DeserializationStrategy<T>): T =
        Wire.json.decodeFromString(deserializer, text)

    private suspend fun send(
        method: String,
        path: String,
        query: Map<String, String>,
        body: String?,
        idempotencyKey: String?,
    ): String = withContext(Dispatchers.IO) {
        val url = URL(url(path, query))
        var connection: HttpURLConnection? = null
        try {
            connection = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = timeoutMillis
                readTimeout = timeoutMillis
                instanceFollowRedirects = false
                setRequestProperty("Accept", "application/json")
                extraHeaders.forEach { (key, value) -> setRequestProperty(key, value) }
                tokenProvider?.accessToken()?.let { setRequestProperty("Authorization", "Bearer $it") }
                if (idempotencyKey != null) setRequestProperty("Idempotency-Key", idempotencyKey)
                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                }
            }
            if (body != null) {
                connection.outputStream.use { stream -> stream.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (status == 401) onUnauthorized?.invoke()
            if (status !in 200..299) throw failure(status, text)
            text
        } catch (api: VoxDeskApiException) {
            throw api
        } catch (io: IOException) {
            throw VoxDeskNetworkException("request to $path failed", io)
        } finally {
            connection?.disconnect()
        }
    }

    private fun failure(status: Int, text: String): VoxDeskApiException {
        val envelope = runCatching {
            Wire.json.decodeFromString(ApiErrorEnvelope.serializer(), text)
        }.getOrNull()
        val body = envelope?.detail
        return VoxDeskApiException(
            status = status,
            code = body?.code?.takeIf { it.isNotBlank() } ?: "error",
            message = body?.message?.takeIf { it.isNotBlank() }
                ?: text.take(300).ifBlank { "request failed with status $status" },
            detail = body?.detail,
        )
    }

    companion object {
        /** A fresh idempotency key. Persist it alongside the intent if you might retry. */
        fun newIdempotencyKey(): String = UUID.randomUUID().toString()

        private fun encode(value: String): String =
            java.net.URLEncoder.encode(value, "UTF-8").replace("+", "%20")
    }
}
