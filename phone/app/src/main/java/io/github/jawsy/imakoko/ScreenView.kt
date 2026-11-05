package io.github.jawsy.imakoko

import android.content.Context
import android.content.res.ColorStateList
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.LinearLayout
import android.widget.TextView

/** Puts what Screen says on activity_main: the chip, the last notice, the 見守り box and the setup. */
object ScreenView {
    interface Actions {
        fun open(url: String)
        fun fix(f: Screen.Fix)
    }

    fun draw(root: View, s: Screen.State, editing: Boolean, act: Actions) {
        val chip = Screen.chip(s)
        val ctx = root.context
        root.findViewById<TextView>(R.id.chip).apply {
            text = dotted(chip.text, ctx.getColor(color(chip.kind)))
            backgroundTintList = ColorStateList.valueOf(ctx.getColor(chipBg(chip.kind)))
        }
        notice(root, Screen.card(s.notice, s.now), act)
        watch(root, s, act)
        setup(root, s, editing)
    }

    private fun notice(root: View, c: Screen.Card?, act: Actions) {
        val ctx = root.context
        val card = root.findViewById<View>(R.id.notice_card)
        card.visibility = if (c == null) View.GONE else View.VISIBLE
        root.findViewById<View>(R.id.empty_card).visibility = if (c == null) View.VISIBLE else View.GONE
        if (c == null) return
        // the stripe in the level color keeps to the card's round corners
        card.clipToOutline = true
        root.findViewById<View>(R.id.notice_stripe).setBackgroundColor(Look.color(c.level) ?: ctx.getColor(R.color.accent))
        root.findViewById<TextView>(R.id.notice_title).text = c.title
        root.findViewById<TextView>(R.id.notice_when).apply {
            text = c.head
            visibility = shown(c.head)
        }
        root.findViewById<TextView>(R.id.notice_notes).apply {
            text = c.notes
            visibility = shown(c.notes)
        }
        root.findViewById<View>(R.id.pages_label).visibility = if (c.pages.isEmpty()) View.GONE else View.VISIBLE
        val box = root.findViewById<LinearLayout>(R.id.links)
        box.removeAllViews()
        c.pages.forEachIndexed { i, (name, url) ->
            if (i > 0) box.addView(divider(ctx))
            box.addView(page(ctx, name) { act.open(url) })
        }
        root.findViewById<TextView>(R.id.notice_meta).apply {
            text = c.checked
            visibility = shown(c.checked)
        }
    }

    private fun watch(root: View, s: Screen.State, act: Actions) {
        val ctx = root.context
        val rows = Screen.rows(s)
        val set = s.project != null
        val card = if (set || rows.isNotEmpty()) View.VISIBLE else View.GONE
        root.findViewById<View>(R.id.watch_label).visibility = card
        root.findViewById<View>(R.id.watch_card).visibility = card
        val box = root.findViewById<LinearLayout>(R.id.rows)
        box.removeAllViews()
        for (r in rows) box.addView(row(ctx, r, act))
        // a button that cannot be pressed now is left out, not grayed
        root.findViewById<View>(R.id.start).visibility = if (set && !s.tracking) View.VISIBLE else View.GONE
        root.findViewById<View>(R.id.stop).visibility = if (s.tracking) View.VISIBLE else View.GONE
    }

    private fun setup(root: View, s: Screen.State, editing: Boolean) {
        val column = root.findViewById<LinearLayout>(R.id.column)
        val part = root.findViewById<View>(R.id.setup_part)
        val set = s.project != null
        // until the setup is saved it is the first thing to do, so it sits at the top;
        // after that it goes down above the privacy note. Moved only when it must, so the paste box keeps its focus.
        if (column.indexOfChild(part) != if (set) column.childCount - 2 else 0) {
            column.removeView(part)
            column.addView(part, if (set) column.childCount - 1 else 0)
        }
        root.findViewById<View>(R.id.setup_done).visibility = if (set) View.VISIBLE else View.GONE
        if (set) root.findViewById<TextView>(R.id.setup_state).text = "済み（Firebase ${s.project}）"
        root.findViewById<TextView>(R.id.setup_toggle).setText(if (editing) R.string.close else R.string.change)
        val box = root.findViewById<View>(R.id.setup_box)
        box.visibility = if (!set || editing) View.VISIBLE else View.GONE
        box.setPadding(0, if (set) dp(root.context, 12) else 0, 0, 0)
        val result = root.findViewById<TextView>(R.id.setup_result)
        result.visibility = shown(result.text)
    }

    private fun row(ctx: Context, r: Screen.Row, act: Actions): View {
        val line = LinearLayout(ctx)
        line.orientation = LinearLayout.HORIZONTAL
        line.gravity = Gravity.CENTER_VERTICAL
        line.setPadding(0, dp(ctx, 8), 0, dp(ctx, 8))
        val dot = text(ctx, "●", 11f, color(r.kind))
        line.addView(dot, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply {
            gravity = Gravity.TOP
            topMargin = dp(ctx, 3)
            marginEnd = dp(ctx, 10)
        })
        val words = text(ctx, r.text, 15f, R.color.text)
        words.setLineSpacing(dp(ctx, 2).toFloat(), 1f)
        line.addView(words, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        r.fix?.let { f ->
            val b = TextView(ctx, null, 0, R.style.Link)
            b.setText(R.string.fix)
            b.setOnClickListener { act.fix(f) }
            line.addView(b, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { marginStart = dp(ctx, 8) })
        }
        return line
    }

    private fun page(ctx: Context, name: String, tap: () -> Unit): View {
        val line = LinearLayout(ctx)
        line.orientation = LinearLayout.HORIZONTAL
        line.gravity = Gravity.CENTER_VERTICAL
        line.minimumHeight = dp(ctx, 52)
        line.setPadding(dp(ctx, 18), dp(ctx, 10), dp(ctx, 16), dp(ctx, 10))
        line.setBackgroundResource(R.drawable.row_bg)
        line.setOnClickListener { tap() }
        line.addView(text(ctx, name, 16f, R.color.accent), LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        line.addView(text(ctx, "›", 24f, R.color.sub), LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply {
            marginStart = dp(ctx, 8)
        })
        return line
    }

    private fun divider(ctx: Context): View {
        val v = View(ctx)
        v.setBackgroundColor(ctx.getColor(R.color.line))
        v.layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, dp(ctx, 1)).apply { marginStart = dp(ctx, 18) }
        return v
    }

    private fun text(ctx: Context, s: String, sp: Float, color: Int): TextView {
        val t = TextView(ctx)
        t.text = s
        t.textSize = sp
        t.setTextColor(ctx.getColor(color))
        return t
    }

    private fun dotted(s: String, color: Int): CharSequence {
        val out = SpannableString("●  $s")
        out.setSpan(ForegroundColorSpan(color), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        return out
    }

    private fun color(k: Screen.Kind) = when (k) {
        Screen.Kind.OK -> R.color.ok
        Screen.Kind.IDLE -> R.color.idle
        Screen.Kind.WARN -> R.color.warn
        Screen.Kind.BAD -> R.color.bad
        Screen.Kind.INFO -> R.color.accent
    }

    private fun chipBg(k: Screen.Kind) = when (k) {
        Screen.Kind.OK -> R.color.chip_ok
        Screen.Kind.WARN -> R.color.chip_warn
        Screen.Kind.BAD -> R.color.chip_bad
        Screen.Kind.IDLE, Screen.Kind.INFO -> R.color.chip_idle
    }

    private fun shown(s: CharSequence) = if (s.isEmpty()) View.GONE else View.VISIBLE

    private fun dp(ctx: Context, n: Int) = (n * ctx.resources.displayMetrics.density).toInt()
}
