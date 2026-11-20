<template>
  <div>
    <div class="hero">
      <div>
        <h1>批次快照</h1>
        <p>三腿每笔装载尝试的事件湖。口径 {{ rule }}：同供应商+货号以首行业务日为锚，跨日申报改挂锚日（folded_count=1，申报日原样留痕）；锚日封死后改挂记 quarantined_sealed 并进隔离区，正式表零写入。</p>
      </div>
      <button @click="load">刷新</button>
    </div>
    <div class="panel">
      <div class="row">
        <input v-model="filterDay" placeholder="按锚日过滤 YYYY-MM-DD" />
        <select v-model="filterLeg">
          <option value="">全部腿</option>
          <option value="expect">预报</option>
          <option value="receive">实收</option>
          <option value="confirm">回执</option>
        </select>
        <select v-model="filterResult">
          <option value="">全部结果</option>
          <option value="accepted">accepted</option>
          <option value="rejected_sealed">rejected_sealed</option>
          <option value="quarantined_sealed">quarantined_sealed</option>
        </select>
      </div>
      <table>
        <thead>
          <tr><th>腿</th><th>单号</th><th>供应商</th><th>货号</th><th>申报日</th><th>锚日闭包</th><th>发生时刻(审计)</th><th>改挂</th><th>结果</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in rows" :key="e.id">
            <td>{{ e.leg }}</td><td>{{ e.line_no }}</td><td>{{ e.supplier_code }}</td><td>{{ e.sku }}</td>
            <td>{{ e.declared_dock_day }}</td><td>{{ e.closed_dock_day }}</td>
            <td>{{ e.occurred_at || '—' }}</td>
            <td><span class="tag" :class="e.folded_count ? 'seal' : 'ok'">{{ e.folded_count ? '改挂' : '原位' }}</span></td>
            <td><span class="tag" :class="e.result === 'accepted' ? 'ok' : 'bad'">{{ e.result }}</span></td>
            <td>{{ e.reason || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="msg">无快照事件</p>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const filterDay = ref('')
const filterLeg = ref('')
const filterResult = ref('')
const rule = 'first_complete_anchor_day'
async function load() {
  const qs = new URLSearchParams()
  if (filterDay.value) qs.set('dock_day', filterDay.value)
  if (filterLeg.value) qs.set('leg', filterLeg.value)
  if (filterResult.value) qs.set('result', filterResult.value)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  rows.value = await api(`/ingest-events${suffix}`)
}
onMounted(load)
</script>
