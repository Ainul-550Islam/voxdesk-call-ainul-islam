// Root build file: nothing but plugin versions. Everything a library module needs is
// declared in the module, so adding a second artifact does not inherit surprises.
plugins {
    id("com.android.library") version "8.7.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.0.21" apply false
}
