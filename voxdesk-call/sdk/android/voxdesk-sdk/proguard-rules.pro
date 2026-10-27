# Keep the serialized wire models: kotlinx.serialization resolves them reflectively.
-keepclassmembers class com.voxdesk.sdk.** {
    *** Companion;
}
-keepclasseswithmembers class com.voxdesk.sdk.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.voxdesk.sdk.**$$serializer { *; }
