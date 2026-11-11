from django.contrib import admin

from apps.core.admin_cards import CardListAdminMixin, short_dt

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("role", "content", "meta", "created_at")
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(CardListAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "channel", "store_content", "created_at")
    list_filter = ("channel", "store_content")
    list_select_related = ("user",)
    inlines = [MessageInline]
    card_tab_filter = "channel"
    add_label = "Додати діалог"

    def card_title(self, obj):
        return f"Діалог №{obj.pk}"

    def card_subtitle(self, obj):
        return (obj.user.get_full_name() or obj.user.get_username()) if obj.user_id else "Гість без акаунта"

    def card_badges(self, obj):
        badges = [(obj.get_channel_display(), "road")]
        badges.append(("Тексти зберігаються", "ok") if obj.store_content else ("Без текстів", "muted"))
        return badges

    def card_meta(self, obj):
        return [("Створено", short_dt(obj.created_at))]
