<template>
  <div>
    <div class="hero">
      <div>
        <h1>隔离区</h1>
        <p>锚日封死后改挂失败的窗外半截行。正式表零写入、不计入任何日闭包；每行在此与批次快照 quarantined_sealed 双留痕，处置后才能离场。</p>
      </div>
      <button @click="load">刷新</button>
    </div>
    <div class="panel">
      <div class="row">
        <input v-model="filterDay" placeholder="按锚日过滤 YYYY-MM-DD" />
      </div>
      <table>
        <thead>
          <tr><th>腿</th><th>单号</th><th>供应商</th><th>货号</th><th>申报日</th><th>锚日(封死)</th><th>数量</th><th>发生时刻(审计)</th><th>隔离原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="q in rows" :key="q.id">
            <td>{{ q.leg }}</td><td>{{ q.line_no }}</td><td>{{ q.supplier_code }}</td><td>{{ q.sku }}</td>
            <td>{{ q.declared_dock_day }}</td><td>{{ q.anchor_day }}</td><td>{{ q.qty }}</td>
            <td>{{ q.occurred_at || '—' }}</td><td>{{ q.reason }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="msg">隔离区为空</p>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const filterDay = ref('')
async function load() {
  const suffix = filterDay.value ? `?anchor_day=${encodeURIComponent(filterDay.value)}` : ''
  rows.value = await api(`/quarantine${suffix}`)
}
onMounted(load)
</script>
