<template>
  <div>
    <div class="hero">
      <div>
        <h1>总览</h1>
        <p>当前锚日三腿行数、对账批次与结案状态。扫描窗 / 闸位图 / 卡片咬同一日标签；锚日封印后同业务键改挂一律进隔离区。</p>
      </div>
      <button @click="load">刷新</button>
    </div>
    <div class="grid" v-if="d">
      <div class="stat"><div class="k">锚日闭包</div><div class="v">{{ d.dock_day }}</div></div>
      <div class="stat"><div class="k">落日口径</div><div class="v">{{ d.fold_rule }}</div></div>
      <div class="stat"><div class="k">预报 / 实收 / 回执</div><div class="v">{{ d.expect_count }} / {{ d.receive_count }} / {{ d.confirm_count }}</div></div>
      <div class="stat"><div class="k">未决差额</div><div class="v">{{ d.pending_variance }}</div></div>
      <div class="stat">
        <div class="k">结案闸（同标签）</div>
        <div class="v"><span class="tag" :class="d.sealed ? 'seal' : 'bad'">{{ d.sealed ? '已封印' : '未封印' }}</span></div>
      </div>
      <div class="stat"><div class="k">受理 / 封印拒绝 / 隔离</div><div class="v">{{ d.ingest_accepted_count }} / {{ d.ingest_rejected_sealed_count }} / {{ d.ingest_quarantined_count }}</div></div>
      <div class="stat"><div class="k">改挂行数</div><div class="v">{{ d.ingest_folded_count }}</div></div>
      <div class="stat">
        <div class="k">计数自证（正式行=受理快照）</div>
        <div class="v"><span class="tag" :class="d.counts_consistent ? 'ok' : 'bad'">{{ d.formal_total }} = {{ d.ingest_accepted_count }} {{ d.counts_consistent ? '一致' : '撒谎' }}</span></div>
      </div>
    </div>
    <div class="panel" v-if="d && d.quarantine_count">
      <h2>隔离区有待处置行 {{ d.quarantine_count }} 条</h2>
      <p><router-link to="/quarantine">前往隔离区查看 →</router-link></p>
    </div>
    <div class="panel" v-if="d && d.latest_batch">
      <h2>最近对账批次</h2>
      <p>{{ d.latest_batch.batch_no }} · 口径 {{ d.latest_batch.fold_rule }} · 对齐 {{ d.latest_batch.matched_count }} · 差额 {{ d.latest_batch.variance_count }} · {{ d.latest_batch.status }}</p>
    </div>
    <p class="msg">{{ err }}</p>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const d = ref(null)
const err = ref('')
async function load() {
  err.value = ''
  try { d.value = await api('/dashboard') } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
